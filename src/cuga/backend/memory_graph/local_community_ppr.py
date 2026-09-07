from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import math

from .graph import MemoryGraph, MemoryGraphError


@dataclass(frozen=True)
class LocalCommunityConfig:
    """Configuration for seeded local community detection.

    Communities are discovered on the active atomic lateral graph after treating
    every lateral edge as an undirected, unweighted connection. Personalized
    PageRank supplies a seed-local ordering and a conductance sweep chooses the
    strongest low-leakage boundary along that ordering.
    """

    restart_probability: float = 0.15
    tolerance: float = 1e-10
    max_iterations: int = 200
    max_accepted_conductance: float = 0.45
    max_sweep_volume_fraction: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 < self.restart_probability < 1.0:
            raise ValueError("restart_probability must be between 0 and 1")
        if self.tolerance <= 0.0:
            raise ValueError("tolerance must be positive")
        if self.max_iterations <= 0:
            raise ValueError("max_iterations must be positive")
        if not 0.0 <= self.max_accepted_conductance <= 1.0:
            raise ValueError("max_accepted_conductance must be between 0 and 1")
        if not 0.0 < self.max_sweep_volume_fraction <= 0.5:
            raise ValueError(
                "max_sweep_volume_fraction must be in the interval (0, 0.5]"
            )


@dataclass(frozen=True)
class LocalCommunityResult:
    seed_id: str
    community_id: str
    node_ids: frozenset[str]
    sweep_conductance: float | None
    accepted_sweep_cut: bool
    connected_component_size: int
    sweep_prefix_size: int | None
    iterations: int


@dataclass(frozen=True)
class SelectedCommunityPartition:
    """One disjoint partition of selected nodes induced by seeded communities."""

    seed_id: str
    community: LocalCommunityResult
    selected_node_ids: frozenset[str]


class LocalCommunityDetector:
    """Seeded PPR + conductance-sweep detector over one MemoryGraph.

    The detector precomputes one undirected adjacency view over active atomic
    nodes and caches seed results. Direction and relation type remain unchanged
    in MemoryGraph itself; they are ignored only for community detection.
    """

    def __init__(
        self,
        graph: MemoryGraph,
        *,
        config: LocalCommunityConfig | None = None,
    ) -> None:
        self.graph = graph
        self.config = config or LocalCommunityConfig()
        self._adjacency = self._build_atomic_undirected_adjacency(graph)
        self._cache: dict[str, LocalCommunityResult] = {}

    @staticmethod
    def _build_atomic_undirected_adjacency(
        graph: MemoryGraph,
    ) -> dict[str, set[str]]:
        atomic_ids = {
            node.id
            for node in graph.atomic_nodes(active_only=True)
        }
        adjacency = {node_id: set() for node_id in atomic_ids}

        # lateral_edges() is incident/bidirectional by discovery. Iterating once
        # per node and storing neighbors in sets deliberately collapses edge
        # direction and duplicate pair observations for community detection only.
        for node_id in sorted(atomic_ids):
            for edge in graph.lateral_edges(node_id):
                if edge.source_id == node_id:
                    other_id = edge.target_id
                elif edge.target_id == node_id:
                    other_id = edge.source_id
                else:
                    continue

                if other_id == node_id or other_id not in atomic_ids:
                    continue
                adjacency[node_id].add(other_id)
                adjacency[other_id].add(node_id)

        return adjacency

    def community_for_seed(self, seed_id: str) -> LocalCommunityResult:
        cached = self._cache.get(seed_id)
        if cached is not None:
            return cached

        if seed_id not in self._adjacency:
            raise MemoryGraphError(
                f"Local-community seed is not an active atomic node: {seed_id}"
            )

        component = self._connected_component(seed_id)
        if len(component) == 1:
            result = self._make_result(
                seed_id=seed_id,
                node_ids=component,
                sweep_conductance=None,
                accepted_sweep_cut=False,
                connected_component_size=1,
                sweep_prefix_size=None,
                iterations=0,
            )
            self._cache[seed_id] = result
            return result

        scores, iterations = self._personalized_pagerank(
            seed_id=seed_id,
            component=component,
        )
        sweep = self._best_conductance_sweep(
            seed_id=seed_id,
            component=component,
            scores=scores,
        )

        if (
            sweep is not None
            and sweep[1] <= self.config.max_accepted_conductance
        ):
            selected_ids, conductance = sweep
            accepted = True
            sweep_prefix_size: int | None = len(selected_ids)
        else:
            # If no sufficiently low-conductance boundary exists, do not invent
            # an arbitrary split. Treat the full connected atomic component as
            # the local region represented by this seed.
            selected_ids = set(component)
            conductance = sweep[1] if sweep is not None else None
            accepted = False
            sweep_prefix_size = len(sweep[0]) if sweep is not None else None

        result = self._make_result(
            seed_id=seed_id,
            node_ids=selected_ids,
            sweep_conductance=conductance,
            accepted_sweep_cut=accepted,
            connected_component_size=len(component),
            sweep_prefix_size=sweep_prefix_size,
            iterations=iterations,
        )
        self._cache[seed_id] = result
        return result

    def partition_selected_nodes(
        self,
        node_ids: set[str],
        *,
        preferred_seed_id: str | None = None,
        seed_priority: dict[str, int] | None = None,
    ) -> list[SelectedCommunityPartition]:
        """Cover selected atomic nodes with disjoint seed-local communities.

        PPR communities can overlap. For reconstruction we need a deterministic
        partition, so the preferred retrieval anchor claims overlap first, then
        remaining nodes are seeded in traversal-depth/id order. Community
        detection itself still runs against the full atomic lateral graph.
        """
        selected = {
            node_id
            for node_id in node_ids
            if node_id in self._adjacency
        }
        if not selected:
            return []

        priority = seed_priority or {}
        unassigned = set(selected)
        partitions: list[SelectedCommunityPartition] = []

        while unassigned:
            if preferred_seed_id is not None and preferred_seed_id in unassigned:
                seed_id = preferred_seed_id
                preferred_seed_id = None
            else:
                seed_id = min(
                    unassigned,
                    key=lambda node_id: (priority.get(node_id, 10**9), node_id),
                )

            community = self.community_for_seed(seed_id)
            members = unassigned & set(community.node_ids)
            if not members:
                members = {seed_id}

            partitions.append(
                SelectedCommunityPartition(
                    seed_id=seed_id,
                    community=community,
                    selected_node_ids=frozenset(members),
                )
            )
            unassigned.difference_update(members)

        return partitions

    def _connected_component(self, seed_id: str) -> set[str]:
        component = {seed_id}
        queue: deque[str] = deque([seed_id])
        while queue:
            node_id = queue.popleft()
            for neighbor_id in self._adjacency[node_id]:
                if neighbor_id in component:
                    continue
                component.add(neighbor_id)
                queue.append(neighbor_id)
        return component

    def _personalized_pagerank(
        self,
        *,
        seed_id: str,
        component: set[str],
    ) -> tuple[dict[str, float], int]:
        alpha = self.config.restart_probability
        continuation = 1.0 - alpha
        scores = {node_id: 0.0 for node_id in component}
        scores[seed_id] = 1.0

        for iteration in range(1, self.config.max_iterations + 1):
            updated = {node_id: 0.0 for node_id in component}
            updated[seed_id] = alpha
            dangling_mass = 0.0

            for node_id, score in scores.items():
                degree = len(self._adjacency[node_id])
                if degree == 0:
                    dangling_mass += score
                    continue
                share = continuation * score / degree
                for neighbor_id in self._adjacency[node_id]:
                    if neighbor_id in component:
                        updated[neighbor_id] += share

            # Personalized PageRank sends dangling continuation mass back to the
            # seed distribution rather than leaking probability out of the walk.
            if dangling_mass:
                updated[seed_id] += continuation * dangling_mass

            delta = sum(
                abs(updated[node_id] - scores[node_id])
                for node_id in component
            )
            scores = updated
            if delta <= self.config.tolerance:
                return scores, iteration

        return scores, self.config.max_iterations

    def _best_conductance_sweep(
        self,
        *,
        seed_id: str,
        component: set[str],
        scores: dict[str, float],
    ) -> tuple[set[str], float] | None:
        degrees = {
            node_id: len(self._adjacency[node_id])
            for node_id in component
        }
        total_volume = sum(degrees.values())
        if total_volume <= 0:
            return None

        ordering = sorted(
            component,
            key=lambda node_id: (
                -(
                    scores.get(node_id, 0.0) / degrees[node_id]
                    if degrees[node_id] > 0
                    else math.inf if node_id == seed_id else 0.0
                ),
                0 if node_id == seed_id else 1,
                node_id,
            ),
        )

        selected: set[str] = set()
        volume = 0
        boundary_edges = 0
        best_ids: set[str] | None = None
        best_conductance = math.inf
        seed_seen = False
        max_volume = total_volume * self.config.max_sweep_volume_fraction

        for node_id in ordering:
            for neighbor_id in self._adjacency[node_id]:
                if neighbor_id not in component:
                    continue
                if neighbor_id in selected:
                    boundary_edges -= 1
                else:
                    boundary_edges += 1

            selected.add(node_id)
            volume += degrees[node_id]
            if node_id == seed_id:
                seed_seen = True

            complement_volume = total_volume - volume
            if not seed_seen or complement_volume <= 0:
                continue
            if volume > max_volume:
                break

            denominator = min(volume, complement_volume)
            if denominator <= 0:
                continue
            conductance = boundary_edges / denominator

            if (
                conductance < best_conductance - 1e-15
                or (
                    abs(conductance - best_conductance) <= 1e-15
                    and best_ids is not None
                    and len(selected) < len(best_ids)
                )
            ):
                best_conductance = conductance
                best_ids = set(selected)

        if best_ids is None:
            return None
        return best_ids, best_conductance

    @staticmethod
    def _make_result(
        *,
        seed_id: str,
        node_ids: set[str],
        sweep_conductance: float | None,
        accepted_sweep_cut: bool,
        connected_component_size: int,
        sweep_prefix_size: int | None,
        iterations: int,
    ) -> LocalCommunityResult:
        digest = hashlib.sha256(
            "\n".join(sorted(node_ids)).encode("utf-8")
        ).hexdigest()[:12]
        return LocalCommunityResult(
            seed_id=seed_id,
            community_id=f"community-{digest}",
            node_ids=frozenset(node_ids),
            sweep_conductance=sweep_conductance,
            accepted_sweep_cut=accepted_sweep_cut,
            connected_component_size=connected_component_size,
            sweep_prefix_size=sweep_prefix_size,
            iterations=iterations,
        )
