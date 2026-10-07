"""Offline tests for verifier retrieval, prompting, and transport boundaries."""

from __future__ import annotations

import asyncio
import ast
import inspect
import textwrap
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from cuga.backend.cuga_graph.nodes.cuga_agent_core.verification import candidate_calls
from cuga.backend.cuga_graph.nodes.cuga_agent_core.verification import candidate_partition
from cuga.backend.cuga_graph.nodes.cuga_agent_core.verification import model_client
from cuga.backend.cuga_graph.nodes.cuga_agent_core.verification import prompt_verifier
from cuga.backend.cuga_graph.nodes.cuga_agent_core.verification import retrieval_context
from cuga.backend.cuga_graph.nodes.cuga_agent_core.graph.shared_nodes import create_call_model_node
from cuga.backend.cuga_graph.nodes.cuga_agent_core.verification.errors import PromptVerificationError
from cuga.backend.cuga_graph.nodes.cuga_agent_core.verification.system_prompts import (
    REJECTION_ONLY_SYSTEM_PROMPT,
    STANDARD_SYSTEM_PROMPT,
    system_prompt_for_mode,
)
from cuga.backend.memory_graph.graph import MemoryGraph
from cuga.backend.memory_graph.schemas import MemoryNode, NodeKind, SourceReference, SourceSpan, SourceType


def _atom(node_id: str, content: str, start: int = 0) -> MemoryNode:
    return MemoryNode(
        id=node_id,
        session_id="session",
        source_root_id="root",
        kind=NodeKind.ATOMIC_FACT,
        depth=1,
        content=content,
        routing_text=content,
        source_refs=[
            SourceReference(
                source_id="candidate",
                source_type=SourceType.ASSISTANT_MESSAGE,
                span=SourceSpan(start=start, end=start + len(content)),
            )
        ],
    )


def _context(statement: str, *, origin: str = "playbook", atom_id: str = "candidate-a"):
    return retrieval_context._CandidateQueryContextEntry(
        graph_name=origin,
        source_type="document",
        statement_node_id="source-a",
        statement=statement,
        covered_atomic_node_ids={"evidence-a"},
        triggered_by_candidate_atom_ids={atom_id},
    )


def test_query_dedup_preserves_provenance_and_unions_trigger_ids():
    first = _context("Verify customer identity.")
    duplicate = _context(" verify   CUSTOMER identity. ", atom_id="candidate-b")
    duplicate.covered_atomic_node_ids = {"evidence-b"}
    other_origin = _context("Verify customer identity.", origin="context")
    entries, duplicate_count = retrieval_context._deduplicate_candidate_query_context_entries(
        [first, duplicate, other_origin]
    )
    assert entries == [first, other_origin]
    assert duplicate_count == 1
    assert first.statement == "Verify customer identity."
    assert first.covered_atomic_node_ids == {"evidence-a", "evidence-b"}
    assert first.triggered_by_candidate_atom_ids == {"candidate-a", "candidate-b"}


def test_previous_rejection_is_ephemeral_and_prior_q_ids_are_removed():
    entries = [_context("Policy statement")]
    entries[0].context_id = "Q1"
    retrieval_context._append_previous_verifier_rejection_context(
        entries, ("Try transfer", "Q28 and Q17 show a missing approval")
    )
    assert len(entries) == 2
    assert entries[1].context_id == "Q2"
    assert entries[1].graph_name == "verifier_rejection"
    assert "Q28" not in entries[1].statement
    assert "Q17" not in entries[1].statement
    assert "missing approval" in entries[1].statement
    assert retrieval_context._render_candidate_query_context(entries)[1].startswith("Q2 [verifier_rejection]")


def test_combined_evidence_graph_detects_colliding_node_ids():
    first = MemoryGraph()
    second = MemoryGraph()
    first.add_node(_atom("shared", "A"))
    second.add_node(_atom("shared", "B"))
    with pytest.raises(PromptVerificationError, match="duplicate node ID"):
        retrieval_context._combine_evidence_graphs(first, second)


def test_candidate_bulks_use_exact_original_spans_without_rewriting():
    candidate = "First action. Second action."
    graph = MemoryGraph()
    first = _atom("a", "First action.")
    second = _atom("b", "Second action.", start=14)
    graph.add_node(first)
    graph.add_node(second)
    bulks = candidate_partition._build_candidate_verification_bulks(
        candidate=candidate, candidate_graph=graph, candidate_atoms=[second, first]
    )
    assert [bulk.content for bulk in bulks] == ["First action.", "Second action."]
    assert [bulk.atom_ids for bulk in bulks] == [("a",), ("b",)]
    assert all(candidate[bulk.start : bulk.end] == bulk.content for bulk in bulks)


def test_candidate_call_dry_run_resolves_runtime_value_without_calling_tool():
    candidate = "```python\nresult = await lookup(customer_id=customer_id)\n```"
    calls = asyncio.run(
        candidate_calls._extract_candidate_calls_dry_run(
            candidate,
            runtime_variables={"customer_id": "customer-42"},
            tool_names={"lookup"},
        )
    )
    assert len(calls) == 1
    assert calls[0]["call"] == "lookup"
    assert calls[0]["keyword_args"]["customer_id"].static_value == "customer-42"


@pytest.mark.parametrize("source", ["import os", "open('/tmp/file')", "obj.__class__"])
def test_candidate_dry_run_blocks_capability_bearing_code(source: str):
    with pytest.raises(candidate_calls._DryRunBlockedOperation):
        asyncio.run(
            candidate_calls._extract_candidate_calls_dry_run(
                f"```python\n{source}\n```", tool_names={"lookup"}
            )
        )


def test_verifier_decision_validation_normalizes_known_ids_and_rejects_unknown():
    entry = _context("Identity must be verified.")
    entry.context_id = "Q38"
    decision = prompt_verifier.CandidateContextDecision(
        verdict="rejected", reason="The candidate bypasses identity verification.", violated_context_ids=["38"]
    )
    prompt_verifier._validate_context_decision(decision, [entry])
    assert decision.violated_context_ids == ["Q38"]
    decision.violated_context_ids = ["Q39"]
    with pytest.raises(PromptVerificationError, match="unknown candidate-query-context IDs"):
        prompt_verifier._validate_context_decision(decision, [entry])
    decision.verdict = "approved"
    decision.violated_context_ids = ["Q38"]
    with pytest.raises(PromptVerificationError, match="approved candidate"):
        prompt_verifier._validate_context_decision(decision, [entry])


def test_atom_decision_aggregation_prioritizes_contradiction_and_deduplicates_reasons():
    decision = prompt_verifier.VerificationDecision(
        atoms=[
            prompt_verifier.CandidateAtomDecision(
                candidate_atom_id="a", verdict="insufficient", reason="No support"
            ),
            prompt_verifier.CandidateAtomDecision(
                candidate_atom_id="b", verdict="contradicted", reason="Wrong account"
            ),
            prompt_verifier.CandidateAtomDecision(
                candidate_atom_id="c", verdict="contradicted", reason="Wrong account"
            ),
        ]
    )
    prompt_verifier._validate_atom_decisions(decision, ["a", "b", "c"])
    assert prompt_verifier._aggregate_atom_decisions(decision) == (
        False, "Wrong account No support", "contradicted"
    )
    with pytest.raises(PromptVerificationError, match="duplicate candidate_atom_id"):
        prompt_verifier._validate_atom_decisions(
            prompt_verifier.VerificationDecision(atoms=[decision.atoms[0], decision.atoms[0]]), ["a"]
        )


@pytest.mark.parametrize("reject_only", [False, True])
def test_verifier_call_uses_selected_mode_and_untouched_candidate(monkeypatch, reject_only: bool):
    candidate = "Once approved, I can transfer funds."
    graph = MemoryGraph()
    graph.add_node(_atom("candidate", candidate))
    empty = MemoryGraph()
    entry = _context("Transfers require approval.")
    entry.context_id = "Q1"
    monkeypatch.setattr(
        prompt_verifier,
        "settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(prompt_verification_rejection_only=reject_only)
        ),
    )
    monkeypatch.setattr(prompt_verifier, "_extract_candidate_calls", _empty_calls)
    monkeypatch.setattr(prompt_verifier, "_build_candidate_query_context", lambda **_: [entry])
    captured = {}

    async def fake_invoke(**kwargs):
        captured.update(kwargs)
        return prompt_verifier.CandidateContextDecision(verdict="approved")

    monkeypatch.setattr(prompt_verifier, "invoke_verifier_decision", fake_invoke)
    decision = asyncio.run(
        prompt_verifier._verify_with_graphs(
            candidate=candidate,
            candidate_kind="terminal",
            candidate_graph=graph,
            state_graph=empty,
            execution_graph=empty,
            knowledge_base_graph=empty,
            cuga_policy_graph=empty,
            playbook_graph=empty,
        )
    )
    assert decision.verdict == "approved"
    assert captured["messages"][0].content == system_prompt_for_mode(rejection_only=reject_only)
    assert captured["messages"][0].content == (
        REJECTION_ONLY_SYSTEM_PROMPT if reject_only else STANDARD_SYSTEM_PROMPT
    )
    assert f"[RAW_CANDIDATE]\n{candidate}" in captured["messages"][1].content
    assert "Q1 [playbook]: Transfers require approval." in captured["messages"][1].content


async def _empty_calls(*args, **kwargs):
    return []


def test_model_settings_are_transport_only_and_do_not_mutate_base(monkeypatch):
    base = {
        "model": "old",
        "temperature": 0.2,
        "top_p": 0.7,
        "extra_params": {"top_p": 0.7, "keep": "yes"},
    }
    monkeypatch.setattr(model_client, "settings", SimpleNamespace(agent=SimpleNamespace(code=SimpleNamespace(model=base))))
    monkeypatch.setattr(model_client, "PROMPT_VERIFIER_MODEL_NAME", "gpt-5.6-sol")
    gpt = model_client._verifier_model_settings()
    assert "temperature" not in gpt and "top_p" not in gpt
    assert gpt["extra_params"] == {"keep": "yes"}
    assert base["temperature"] == 0.2
    monkeypatch.setattr(model_client, "PROMPT_VERIFIER_MODEL_NAME", "aws/claude-haiku-4-5")
    claude = model_client._verifier_model_settings()
    assert claude["max_tokens"] == 64000
    assert claude["extra_params"]["thinking"]["budget_tokens"] < claude["max_tokens"]
    assert "thinking" not in base["extra_params"]


def test_gemini_and_oss_model_settings_take_distinct_transport_paths(monkeypatch):
    base = {
        "model": "old",
        "temperature": 0.2,
        "top_p": 0.7,
        "reasoning_effort": "medium",
        "extra_params": {"thinking_level": "low", "keep": "yes"},
    }
    monkeypatch.setattr(model_client, "settings", SimpleNamespace(agent=SimpleNamespace(code=SimpleNamespace(model=base))))
    monkeypatch.setattr(model_client, "PROMPT_VERIFIER_MODEL_NAME", "gcp/gemini-3.7-flash")
    gemini = model_client._verifier_model_settings()
    assert gemini["model"] == "gcp/gemini-3.7-flash"
    assert "temperature" not in gemini and "top_p" not in gemini
    assert "reasoning_effort" not in gemini
    assert gemini["extra_params"] == {"keep": "yes"}
    monkeypatch.setattr(model_client, "PROMPT_VERIFIER_MODEL_NAME", "openai/gpt-oss-120b")
    oss = model_client._verifier_model_settings()
    assert oss["model"] == "openai/gpt-oss-120b"
    assert oss["temperature"] == 0.2
    assert oss["extra_params"] == base["extra_params"]


def test_claude_thinking_budget_rejects_invalid_bounds(monkeypatch):
    monkeypatch.setattr(model_client, "PROMPT_VERIFIER_CLAUDE_MAX_TOKENS", 1024)
    with pytest.raises(PromptVerificationError, match="MAX_TOKENS"):
        model_client._validated_claude_thinking_budget()
    monkeypatch.setattr(model_client, "PROMPT_VERIFIER_CLAUDE_MAX_TOKENS", 2048)
    monkeypatch.setattr(model_client, "PROMPT_VERIFIER_CLAUDE_THINKING_BUDGET_TOKENS", 2048)
    with pytest.raises(PromptVerificationError, match="strictly less"):
        model_client._validated_claude_thinking_budget()


def test_agent_verifier_calls_use_supported_keyword_arguments():
    call_tree = ast.parse(textwrap.dedent(inspect.getsource(create_call_model_node)))
    supported = set(inspect.signature(prompt_verifier.verify_candidate).parameters)
    verifier_calls = [
        node
        for node in ast.walk(call_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "verify_candidate"
    ]
    assert len(verifier_calls) == 2
    for call in verifier_calls:
        assert {keyword.arg for keyword in call.keywords} <= supported


@pytest.mark.parametrize("text", ['{"verdict":"approved"}', '```json\n{"verdict":"approved"}\n```', 'Answer: {"verdict":"approved"}'])
def test_model_client_recovers_json_from_supported_text_forms(text: str):
    assert model_client._extract_json_object_from_text(text) == {"verdict": "approved"}
    assert model_client._extract_json_from_message(AIMessage(content=text)) == {"verdict": "approved"}


def test_model_client_falls_back_when_structured_output_is_unparseable(monkeypatch):
    class Structured:
        async def ainvoke(self, messages):
            return {"parsed": None, "raw": AIMessage(content="unparseable"), "parsing_error": "bad JSON"}

    class Model:
        model_name = "gpt-5.6-sol"

        def with_structured_output(self, schema, *, method, include_raw):
            assert schema is prompt_verifier.CandidateContextDecision
            assert method == "function_calling" and include_raw is True
            return Structured()

    retries = []

    async def retry(**kwargs):
        retries.append(kwargs)
        return {"verdict": "approved"}

    monkeypatch.setattr(model_client, "_get_model", lambda **_: Model())
    monkeypatch.setattr(model_client, "_plain_json_retry", retry)
    result = asyncio.run(
        model_client.invoke_verifier_decision(
            messages=[],
            schema=prompt_verifier.CandidateContextDecision,
            verification_id="test",
            candidate_kind="terminal",
            candidate_hash="hash",
        )
    )
    assert result.verdict == "approved"
    assert len(retries) == 1
