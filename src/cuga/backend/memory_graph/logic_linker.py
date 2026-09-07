from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from loguru import logger

from .graph import MemoryGraph
from .logging_utils import memory_graph_trace_enabled
from .model_wrapper import call_logic_slot_binding_model
from .schemas import (
    LogicSlot,
    LogicSlotBindingDecision,
    LogicSlotBindingRequest,
    LogicSlotCandidate,
    MemoryNode,
    NodeKind,
)

_MAX_SLOT_CANDIDATES = 16
_MAX_CONTEXT_DEPTH = 5
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def _normalize(text: str) -> str:
    return " ".join(_TOKEN_RE.findall(str(text or "").casefold()))


def _slot_score_text(node_text: str, slot_text: str) -> float:
    node_text = _normalize(node_text)
    slot = _normalize(slot_text)
    if not node_text or not slot:
        return 0.0
    if node_text == slot:
        return 2.0
    node_tokens = set(node_text.split())
    slot_tokens = set(slot.split())
    overlap = len(node_tokens & slot_tokens) / max(1, len(node_tokens | slot_tokens))
    sequence = SequenceMatcher(None, node_text, slot).ratio()
    return overlap + 0.5 * sequence


def logic_slot_match_score(node_text: str, slot_text: str) -> float:
    """Return the lexical pre-ranking score used before slot-identity matching."""
    return _slot_score_text(node_text, slot_text)


def _slot_score(node: MemoryNode, slot_text: str) -> float:
    return logic_slot_match_score(node.routing_text or node.content, slot_text)


def _dedupe_context_paths(paths: list[list[str]]) -> list[list[str]]:
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for path in paths:
        cleaned = [item.strip() for item in path if item and item.strip()]
        if not cleaned:
            continue
        key = tuple(cleaned)
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _node_context_paths(
    graph: MemoryGraph,
    node_id: str,
    *,
    max_depth: int = _MAX_CONTEXT_DEPTH,
) -> list[list[str]]:
    """Return bounded semantic ancestry for one materialized node occurrence.

    ``support_node_ids`` already preserves decomposition parents, including
    composite parents. We intentionally retain occurrence context instead of
    flattening identical leaf strings into one semantic identity.
    """
    if max_depth <= 0:
        return []
    node = graph.nodes.get(node_id)
    if node is None:
        return []

    paths: list[list[str]] = []

    def walk(current: MemoryNode, reverse_path: list[str], seen: set[str]) -> None:
        if len(reverse_path) >= max_depth:
            paths.append(list(reversed(reverse_path)))
            return

        parent_ids = [
            parent_id
            for parent_id in current.support_node_ids
            if parent_id in graph.nodes and parent_id not in seen
        ]
        if not parent_ids:
            if reverse_path:
                paths.append(list(reversed(reverse_path)))
            return

        for parent_id in sorted(parent_ids):
            parent = graph.nodes[parent_id]
            # Raw-source text can be very large and is not useful as a path item;
            # the nearest composite ancestors carry the relevant contextual scope.
            next_path = list(reverse_path)
            if parent.kind != NodeKind.RAW_SOURCE:
                label = (parent.content or parent.routing_text).strip()
                if label:
                    next_path.append(label[:600])
            walk(parent, next_path, {*seen, parent_id})

    walk(node, [], {node_id})
    return _dedupe_context_paths(paths)


def _slot_context_paths(graph: MemoryGraph, slot: LogicSlot) -> list[list[str]]:
    """Collect the originating and bound-occurrence contexts for one slot."""
    paths: list[list[str]] = []

    origin_parent_id = slot.metadata.get("logic_parent_node_id")
    if isinstance(origin_parent_id, str) and origin_parent_id in graph.nodes:
        origin_parent = graph.nodes[origin_parent_id]
        ancestor_paths = _node_context_paths(graph, origin_parent_id)
        if ancestor_paths:
            paths.extend(
                [*path, origin_parent.content[:600]]
                for path in ancestor_paths
            )
        elif origin_parent.kind != NodeKind.RAW_SOURCE:
            paths.append([origin_parent.content[:600]])

    for binding in slot.bindings:
        paths.extend(_node_context_paths(graph, binding.node_id))

    return _dedupe_context_paths(paths)


def match_logic_node_to_slot_candidates(
    *,
    node_id: str,
    node_content: str,
    node_routing_text: str,
    candidates: list[LogicSlotCandidate],
    node_context_paths: list[list[str]] | None = None,
    max_candidates: int = _MAX_SLOT_CANDIDATES,
) -> list[LogicSlotBindingDecision]:
    """Reuse the contextual identity matcher outside a ``MemoryGraph``.

    Lexical similarity only pre-ranks candidates. The LLM receives the node and
    slot occurrence ancestry and decides whether the *complete contextual
    propositions* are identical. Identical strings under unrelated parents are
    therefore not merged automatically.
    """
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")
    if not candidates:
        return []

    ranked = sorted(
        (
            (_slot_score_text(node_routing_text or node_content, slot.source_text), slot)
            for slot in candidates
            if node_id not in slot.bound_node_ids
        ),
        key=lambda item: (-item[0], item[1].slot_id),
    )
    selected = [slot for _, slot in ranked[:max_candidates]]
    if not selected:
        return []

    response = call_logic_slot_binding_model(
        LogicSlotBindingRequest(
            node_id=node_id,
            node_content=node_content,
            node_routing_text=node_routing_text,
            node_context_paths=_dedupe_context_paths(node_context_paths or []),
            candidates=selected,
        )
    )
    allowed = {slot.slot_id for slot in selected}
    unique: dict[tuple[str, bool], LogicSlotBindingDecision] = {}
    for decision in response.bindings:
        if decision.slot_id not in allowed:
            continue
        unique[(decision.slot_id, decision.value)] = decision
    return [unique[key] for key in sorted(unique)]


def match_nodes_to_logic_slots(
    *,
    source_graph: MemoryGraph,
    node_ids: set[str],
    target_graphs: Iterable[MemoryGraph],
    bind: bool = False,
    max_candidates: int = _MAX_SLOT_CANDIDATES,
) -> dict[str, list[LogicSlotBindingDecision]]:
    """Match semantic occurrences to contextually identical persistent slots.

    Occurrence nodes remain separate in the semantic graph. ``bind=True`` only
    unifies their logical identity when the complete proposition, including
    inherited parent context, matches. Uncertain identity remains unbound.
    """
    if max_candidates <= 0:
        raise ValueError("max_candidates must be positive")

    slot_owner: dict[str, MemoryGraph] = {}
    slot_by_id: dict[str, LogicSlot] = {}
    for graph in target_graphs:
        for slot in graph.logic_layer.slots:
            if slot.id in slot_owner and slot_owner[slot.id] is not graph:
                raise ValueError(f"Duplicate logic slot ID across graphs: {slot.id}")
            slot_owner[slot.id] = graph
            slot_by_id[slot.id] = slot

    if not slot_by_id:
        return {}

    results: dict[str, list[LogicSlotBindingDecision]] = {}
    for node_id in sorted(node_ids):
        node = source_graph.nodes.get(node_id)
        if node is None or node.kind != NodeKind.ATOMIC_FACT:
            continue

        matched = match_logic_node_to_slot_candidates(
            node_id=node.id,
            node_content=node.content,
            node_routing_text=node.routing_text,
            node_context_paths=_node_context_paths(source_graph, node.id),
            candidates=[
                LogicSlotCandidate(
                    slot_id=slot.id,
                    source_text=slot.source_text,
                    bound_node_ids=list(slot.bound_node_ids),
                    context_paths=_slot_context_paths(slot_owner[slot.id], slot),
                )
                for slot in slot_by_id.values()
            ],
            max_candidates=max_candidates,
        )
        if not matched:
            continue

        persisted: list[LogicSlotBindingDecision] = []
        for decision in matched:
            owner = slot_owner.get(decision.slot_id)
            if owner is None:
                continue
            persisted.append(decision)
            if bind:
                owner.bind_logic_slot(decision.slot_id, node.id, value=decision.value)

        if persisted:
            unique = {(item.slot_id, item.value): item for item in persisted}
            results[node.id] = [unique[key] for key in sorted(unique)]
            if memory_graph_trace_enabled():
                logger.info(
                    "Logic slot match: node_id={} bind={} slots={} contextual_paths={}",
                    node.id,
                    bind,
                    [(item.slot_id, item.value) for item in results[node.id]],
                    len(_node_context_paths(source_graph, node.id)),
                )

    return results


def link_new_nodes_to_logic_slots(
    *,
    source_graph: MemoryGraph,
    new_node_ids: set[str],
    target_graphs: Iterable[MemoryGraph],
    max_candidates: int = _MAX_SLOT_CANDIDATES,
) -> dict[str, list[LogicSlotBindingDecision]]:
    """Persist bindings for already-verified/newly committed semantic nodes."""
    return match_nodes_to_logic_slots(
        source_graph=source_graph,
        node_ids=new_node_ids,
        target_graphs=target_graphs,
        bind=True,
        max_candidates=max_candidates,
    )