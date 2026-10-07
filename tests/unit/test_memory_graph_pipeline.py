"""Offline regression tests for the memory-graph pipeline boundaries."""

from __future__ import annotations

import importlib
import pkgutil

import pytest
from pydantic import ValidationError

from cuga.backend.memory_graph.atomic_payload_grounding import _sanitize_atomic_payload
from cuga.backend import memory_graph
from cuga.backend.memory_graph import builder as graph_builder
from cuga.backend.memory_graph.document_structure import (
    build_source_blocks,
    parse_table_row,
    structural_terminal_artifact_issue,
    table_row_to_block,
)
from cuga.backend.memory_graph.graph import MemoryGraph, MemoryGraphError
from cuga.backend.memory_graph.graph_serialization import (
    GraphSerializationError,
    compute_prompt_hash,
    deserialize_graph,
    serialize_graph,
)
from cuga.backend.memory_graph.model_decisions import (
    ChunkSemanticAuditResult,
    DecompositionAuditResult,
    LogicNormalizationClause,
    LogicNormalizationDecision,
)
from cuga.backend.memory_graph.relation_linker import link_new_nodes
from cuga.backend.memory_graph.retrieval import score_pair
from cuga.backend.memory_graph.schemas import (
    CreationMethod,
    DecompositionDraft,
    DraftHierarchyEdge,
    DraftNode,
    EdgeFamily,
    GraphBuildRequest,
    LogicLayer,
    LogicSlot,
    LocalLogicOperand,
    MemoryEdge,
    MemoryNode,
    NodeKind,
    PropositionPayload,
    RelationType,
    SourceReference,
    SourceSpan,
    SourceType,
)
from cuga.backend.memory_graph.source_chunking import split_source_for_decomposition
from cuga.backend.memory_graph.validation import DecompositionValidator


def _node(node_id: str, content: str, *, kind: NodeKind = NodeKind.ATOMIC_FACT, depth: int = 1) -> MemoryNode:
    return MemoryNode(
        id=node_id,
        session_id="session",
        source_root_id="root",
        kind=kind,
        depth=depth,
        content=content,
        routing_text=content,
        source_refs=[
            SourceReference(
                source_id="source",
                source_type=SourceType.DOCUMENT,
                span=SourceSpan(start=0, end=len(content)),
            )
        ],
    )


def test_memory_graph_modules_import_without_optional_model_runtime():
    names = [module.name for module in pkgutil.iter_modules(memory_graph.__path__)]
    assert names
    for name in names:
        importlib.import_module(f"{memory_graph.__name__}.{name}")


def test_document_blocks_preserve_heading_scope_and_table_provenance():
    source = "# Handbook\n\n## Access\n\nNOTE: Verify identity.\n\n| Tool | When to use |\n| --- | --- |\n| lookup | Search a record. |"
    blocks = build_source_blocks(source)
    assert [block.text for block in blocks][:3] == ["Handbook", "Access", "Verify identity."]
    assert blocks[2].metadata["formatting_label"] == "NOTE"
    assert blocks[2].metadata["active_heading"] == "Access"
    assert blocks[-1].text == "Search a record."
    assert blocks[-1].metadata["Tool"] == "lookup"
    assert blocks[-1].metadata["source_column"] == "When to use"


def test_document_structure_does_not_promote_labels_to_facts():
    assert structural_terminal_artifact_issue("**Inputs:**") == "standalone_structural_label"
    assert structural_terminal_artifact_issue("***") == "punctuation_only"
    assert structural_terminal_artifact_issue("Verify identity.") is None
    assert parse_table_row("| Tool | Use |") == ["Tool", "Use"]
    assert parse_table_row("Tool | Use") is None
    assert table_row_to_block(["Name", "When to use"], ["lookup", "Search records."]) == (
        "Search records.",
        {"Name": "lookup", "source_column": "When to use"},
    )


@pytest.mark.parametrize(
    "source",
    [
        "",
        "A short sentence.",
        "# Section\n\n" + "A sentence. " * 90,
        "```python\n" + "x = 1\n" * 80 + "```\n",
    ],
)
def test_source_chunks_remain_exact_contiguous_source_slices(source: str):
    chunks = split_source_for_decomposition(source, max_chars=120, target_chars=100)
    assert "".join(chunk.text for chunk in chunks) == source
    assert all(chunk.text == source[chunk.start : chunk.end] for chunk in chunks)
    assert all(len(chunk.text) <= 120 for chunk in chunks)
    assert all(a.end == b.start for a, b in zip(chunks, chunks[1:]))


@pytest.mark.parametrize("max_chars,target_chars", [(0, 1), (10, 0), (10, 11)])
def test_source_chunking_rejects_invalid_limits(max_chars: int, target_chars: int):
    with pytest.raises(ValueError):
        split_source_for_decomposition("text", max_chars=max_chars, target_chars=target_chars)


def test_atomic_payload_removes_ungrounded_fields_and_recovers_source_predicate():
    cleaned, dropped, fallback = _sanitize_atomic_payload(
        source="Do not transfer account funds.",
        semantic_role="prohibition",
        payload=PropositionPayload(
            subjects=["you", "outside actor"],
            predicates=["approve"],
            objects=["account funds", "secret code"],
        ),
    )
    assert cleaned.subjects == ["you"]
    assert cleaned.predicates == ["transfer"]
    assert cleaned.objects == ["account funds"]
    assert dropped == {
        "subjects": ["outside actor"],
        "predicates": ["approve"],
        "objects": ["secret code"],
    }
    assert fallback is True


def test_graph_rejects_invalid_edges_and_deduplicates_symmetric_relations():
    graph = MemoryGraph()
    graph.add_node(_node("root", "Document", kind=NodeKind.RAW_SOURCE, depth=0))
    graph.add_node(_node("a", "Verify identity"))
    graph.add_node(_node("b", "Check account"))
    with pytest.raises(MemoryGraphError, match="Unknown edge target"):
        graph.add_edge(
            MemoryEdge(
                source_id="root",
                target_id="missing",
                family=EdgeFamily.HIERARCHICAL,
                relation=RelationType.DECOMPOSES_INTO,
                creation_method=CreationMethod.DETERMINISTIC,
            )
        )
    graph.add_edge(
        MemoryEdge(
            source_id="root",
            target_id="a",
            family=EdgeFamily.HIERARCHICAL,
            relation=RelationType.DECOMPOSES_INTO,
            creation_method=CreationMethod.DETERMINISTIC,
        )
    )
    first = graph.add_lateral_edge(
        source_id="b", target_id="a", relation=RelationType.RELATED_TO, directed=False
    )
    second = graph.add_lateral_edge(
        source_id="a", target_id="b", relation=RelationType.RELATED_TO, directed=False
    )
    assert first is not None and first.source_id == "a"
    assert second is None
    assert {node.id for node in graph.children("root")} == {"a"}


def test_graph_logic_bindings_are_idempotent_and_conflicts_fail():
    graph = MemoryGraph()
    graph.merge_logic_layer(LogicLayer(slots=[LogicSlot(id="slot", source_text="Identity was verified")]))
    assert graph.bind_logic_slot("slot", "fact") is True
    assert graph.bind_logic_slot("slot", "fact") is False
    with pytest.raises(MemoryGraphError, match="Conflicting logic binding"):
        graph.bind_logic_slot("slot", "fact", value=False)
    assert graph.remove_logic_bindings({"fact"}) == 1
    assert graph.logic_slot("slot").bindings == []


def test_graph_serialization_round_trip_preserves_nodes_edges_and_logic():
    graph = MemoryGraph()
    graph.add_node(_node("root", "Document", kind=NodeKind.RAW_SOURCE, depth=0))
    graph.add_node(_node("a", "Verify identity"))
    graph.add_edge(
        MemoryEdge(
            id="edge",
            source_id="root",
            target_id="a",
            family=EdgeFamily.HIERARCHICAL,
            relation=RelationType.DECOMPOSES_INTO,
            creation_method=CreationMethod.DETERMINISTIC,
        )
    )
    graph.merge_logic_layer(LogicLayer(slots=[LogicSlot(id="slot", source_text="Verify identity")]))
    graph.bind_logic_slot("slot", "a")
    rebuilt = deserialize_graph(serialize_graph(graph))
    assert set(rebuilt.nodes) == {"root", "a"}
    assert set(rebuilt.edges) == {"edge"}
    assert {node.id for node in rebuilt.children("root")} == {"a"}
    assert rebuilt.logic_slot("slot").bound_node_ids == ["a"]
    assert compute_prompt_hash("  policy  ") == compute_prompt_hash("policy")
    assert compute_prompt_hash("policy\nmore") != compute_prompt_hash("policy more")


def test_graph_serialization_rejects_unsupported_version():
    payload = serialize_graph(MemoryGraph())
    payload["format_version"] = -1
    with pytest.raises(GraphSerializationError):
        deserialize_graph(payload)


def test_relation_linker_never_invents_cross_source_edges():
    graph = MemoryGraph()
    graph.add_node(_node("a", "Check identity"))
    graph.add_node(_node("b", "Check account"))
    assert link_new_nodes(graph, {"a"}) == []
    assert graph.edges == {}
    with pytest.raises(ValueError, match="unknown graph node IDs"):
        link_new_nodes(graph, {"missing"})


def test_retrieval_falls_back_to_lexical_score_without_embeddings():
    anchor = _node("a", "Verify customer identity")
    candidate = _node("b", "Verify customer identity")
    result = score_pair(anchor, candidate)
    assert result.used_embedding is False
    assert result.embedding_score is None
    assert result.combined_score == result.lexical_score
    assert result.combined_score > 0
    with pytest.raises(ValueError):
        score_pair(anchor, candidate, lexical_weight=-1)


def test_structured_decisions_enforce_audit_contracts():
    DecompositionAuditResult(action="pass")
    with pytest.raises(ValidationError, match="passing decomposition audit"):
        DecompositionAuditResult(
            action="pass",
            corrected_decision={
                "kind": "composite",
                "routing_text": "rule",
                "children": [
                    {
                        "content": "Verify identity.",
                        "source_text": "Verify identity.",
                        "semantic_role": "requirement",
                    }
                ],
            },
        )
    with pytest.raises(ValidationError, match="must identify at least one issue"):
        ChunkSemanticAuditResult(complete=False)
    with pytest.raises(ValidationError, match="Assertion clause requires root"):
        LogicNormalizationClause(kind="assertion", evidence_text="fact")


def test_logic_normalization_rejects_unknown_expression_reference():
    with pytest.raises(ValidationError, match="unknown expression_id"):
        LogicNormalizationDecision(
            clauses=[
                LogicNormalizationClause(
                    kind="assertion",
                    root=LocalLogicOperand(expression_id=1),
                    evidence_text="fact",
                )
            ]
        )


def test_validator_rejects_unreachable_and_unknown_hierarchy_nodes():
    request = GraphBuildRequest(
        session_id="session", source_id="source", source_type=SourceType.DOCUMENT, content="Verify identity."
    )
    draft = DecompositionDraft(
        nodes=[
            DraftNode(
                temporary_id="a",
                kind=NodeKind.ATOMIC_FACT,
                content="Verify identity.",
                routing_text="Verify identity.",
                source_spans=[SourceSpan(start=0, end=16)],
                proposition=PropositionPayload(predicates=["verify"]),
            )
        ],
        hierarchy=[DraftHierarchyEdge(parent_temporary_id="missing", child_temporary_id="a")],
    )
    validation = DecompositionValidator().validate(request=request, draft=draft)
    codes = {issue.code for issue in validation.issues}
    assert validation.accepted is False
    assert "unknown_hierarchy_parent" in codes
    assert "unreachable_node" in codes


def test_graph_builder_materializes_one_atomic_source_without_external_models(monkeypatch):
    monkeypatch.setattr(graph_builder, "retrieval_embeddings_enabled", lambda: False)
    monkeypatch.setattr(graph_builder, "classify_terminal_for_retrieval", lambda text: ("proposition", []))

    def payloads(request, *, leaves):
        return {
            leaf["temporary_id"]: PropositionPayload(
                predicates=["verify"], objects=["identity"]
            )
            for leaf in leaves
        }

    monkeypatch.setattr(graph_builder, "extract_atomic_payloads_spacy", payloads)
    builder = graph_builder.GraphBuilder(
        model_callable=lambda request: {"kind": "atomic", "routing_text": request.content}
    )
    result = builder.build(
        GraphBuildRequest(
            session_id="session",
            source_id="source",
            source_type=SourceType.DOCUMENT,
            content="Verify identity.",
            metadata={"skip_logic_enrichment": True},
        )
    )
    assert result.validation.accepted is True
    assert len(result.nodes) == 2
    assert len(result.edges) == 1
    atomic = next(node for node in result.nodes if node.kind == NodeKind.ATOMIC_FACT)
    assert atomic.content == "Verify identity."
    assert atomic.proposition.predicates == ["verify"]
    assert atomic.source_refs[0].span == SourceSpan(start=0, end=len("Verify identity."))
