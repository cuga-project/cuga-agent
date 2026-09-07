from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Literal

from .graph import MemoryGraph
from .schemas import (
    LogicExpression,
    LogicLiteral,
    LogicalOperator,
    RelationType,
    SourceType,
)


LogicStatus = Literal["entailed", "contradicted", "undetermined", "inconsistent"]


@dataclass(frozen=True)
class LogicEntailmentResult:
    status: LogicStatus
    queried_slot_ids: tuple[str, ...]
    relevant_rule_ids: tuple[str, ...]
    relevant_assertion_ids: tuple[str, ...]
    established_slot_ids: tuple[str, ...]
    unresolved_slot_ids: tuple[str, ...]


_ALLOWED_FACT_SOURCES = {
    SourceType.POLICY,
    SourceType.DOCUMENT,
    SourceType.TOOL_RESULT,
    SourceType.USER_MESSAGE,
    SourceType.REASONING,
}


_GRAPH_IMPLICATION_RELATIONS = {
    RelationType.IMPLIES,
    RelationType.REQUIRES,
}


def _expression_slot_ids(expression: LogicExpression) -> set[str]:
    if expression.slot_id is not None:
        return {expression.slot_id}
    result: set[str] = set()
    for operand in expression.operands:
        result.update(_expression_slot_ids(operand))
    return result


def _node_source_type(graphs: list[MemoryGraph], node_id: str) -> SourceType | None:
    for graph in graphs:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        if node.source_refs:
            return node.source_refs[0].source_type
        root = graph.nodes.get(node.source_root_id)
        if root and root.source_refs:
            return root.source_refs[0].source_type
    return None


def _node_is_established(graphs: list[MemoryGraph], node_id: str) -> bool:
    for graph in graphs:
        node = graph.nodes.get(node_id)
        if node is None:
            continue
        if not node.logic_asserted:
            return False
        source_type = _node_source_type(graphs, node_id)
        return source_type in _ALLOWED_FACT_SOURCES
    return False


def _collect_relevant_component(
    graphs: list[MemoryGraph],
    seed_slot_ids: set[str],
):
    literal_assertions = [
        item
        for graph in graphs
        for item in graph.logic_layer.literal_assertions
    ]
    relations = [
        item
        for graph in graphs
        for item in graph.logic_layer.relations
    ]
    compound_assertions = [
        item
        for graph in graphs
        for item in graph.logic_layer.compound_assertions
    ]
    compound_rules = [
        item
        for graph in graphs
        for item in graph.logic_layer.compound_rules
    ]
    slots = {
        slot.id: slot
        for graph in graphs
        for slot in graph.logic_layer.slots
    }
    graph_implications = [
        edge
        for graph in graphs
        for edge in graph.edges.values()
        if edge.directed and edge.relation in _GRAPH_IMPLICATION_RELATIONS
    ]
    node_to_slot_bindings: dict[str, list[tuple[str, bool]]] = {}
    for slot in slots.values():
        for binding in slot.bindings:
            node_to_slot_bindings.setdefault(binding.node_id, []).append(
                (slot.id, binding.value)
            )

    relevant_slots = set(seed_slot_ids)
    relevant_literal_assertions = []
    relevant_relations = []
    relevant_compound_assertions = []
    relevant_compound_rules = []
    relevant_graph_implications = []
    seen_ids: set[str] = set()

    changed = True
    while changed:
        changed = False

        for relation in relations:
            if relation.id in seen_ids:
                continue
            refs = {
                relation.antecedent.slot_id,
                relation.consequent.slot_id,
            }
            if refs & relevant_slots:
                seen_ids.add(relation.id)
                relevant_relations.append(relation)
                before = len(relevant_slots)
                relevant_slots.update(refs)
                changed = changed or len(relevant_slots) != before

        for edge in graph_implications:
            if edge.id in seen_ids:
                continue
            endpoint_bindings = [
                *node_to_slot_bindings.get(edge.source_id, []),
                *node_to_slot_bindings.get(edge.target_id, []),
            ]
            refs = {slot_id for slot_id, _ in endpoint_bindings}
            # A graph implication only participates in SAT when both semantic
            # endpoints are represented by logic slots. Graph traversal still
            # handles the relation when one or both endpoints have no slots.
            if (
                not node_to_slot_bindings.get(edge.source_id)
                or not node_to_slot_bindings.get(edge.target_id)
            ):
                continue
            if refs & relevant_slots:
                seen_ids.add(edge.id)
                relevant_graph_implications.append(edge)
                before = len(relevant_slots)
                relevant_slots.update(refs)
                changed = changed or len(relevant_slots) != before

        for rule in compound_rules:
            if rule.id in seen_ids:
                continue
            refs = (
                _expression_slot_ids(rule.condition)
                | _expression_slot_ids(rule.effect)
            )
            if refs & relevant_slots:
                seen_ids.add(rule.id)
                relevant_compound_rules.append(rule)
                before = len(relevant_slots)
                relevant_slots.update(refs)
                changed = changed or len(relevant_slots) != before

        for assertion in literal_assertions:
            if assertion.id in seen_ids:
                continue
            refs = {assertion.literal.slot_id}
            if refs & relevant_slots:
                seen_ids.add(assertion.id)
                relevant_literal_assertions.append(assertion)
                before = len(relevant_slots)
                relevant_slots.update(refs)
                changed = changed or len(relevant_slots) != before

        for assertion in compound_assertions:
            if assertion.id in seen_ids:
                continue
            refs = _expression_slot_ids(assertion.root)
            if refs & relevant_slots:
                seen_ids.add(assertion.id)
                relevant_compound_assertions.append(assertion)
                before = len(relevant_slots)
                relevant_slots.update(refs)
                changed = changed or len(relevant_slots) != before

        # Slots sharing a semantic-node binding denote the same proposition.
        bound_node_ids: set[str] = set()
        for slot_id in list(relevant_slots):
            slot = slots.get(slot_id)
            if slot is None:
                continue
            bound_node_ids.update(binding.node_id for binding in slot.bindings)
        for slot in slots.values():
            if slot.id in relevant_slots:
                continue
            if any(binding.node_id in bound_node_ids for binding in slot.bindings):
                relevant_slots.add(slot.id)
                changed = True

    return (
        slots,
        relevant_slots,
        relevant_literal_assertions,
        relevant_relations,
        relevant_graph_implications,
        relevant_compound_assertions,
        relevant_compound_rules,
    )


class _CNFCompiler:
    def __init__(self, slot_ids: Iterable[str]) -> None:
        self.slot_var: dict[str, int] = {}
        self.next_var = 1
        for slot_id in sorted(set(slot_ids)):
            self.slot_var[slot_id] = self._new_var()
        self.clauses: list[list[int]] = []

    def _new_var(self) -> int:
        value = self.next_var
        self.next_var += 1
        return value

    def compile_literal(self, literal: LogicLiteral) -> int:
        var = self.slot_var[literal.slot_id]
        return var if literal.value else -var

    def compile_expr(self, expression: LogicExpression) -> int:
        if expression.slot_id is not None:
            return self.slot_var[expression.slot_id]

        operands = [self.compile_expr(item) for item in expression.operands]
        operator = expression.operator
        if operator == LogicalOperator.NOT:
            y = self._new_var()
            x = operands[0]
            self.clauses.extend([[-y, -x], [y, x]])
            return y
        if operator == LogicalOperator.AND:
            y = self._new_var()
            for x in operands:
                self.clauses.append([-y, x])
            self.clauses.append([y, *[-x for x in operands]])
            return y
        if operator == LogicalOperator.OR:
            y = self._new_var()
            for x in operands:
                self.clauses.append([-x, y])
            self.clauses.append([-y, *operands])
            return y
        if operator in {
            LogicalOperator.AT_LEAST,
            LogicalOperator.AT_MOST,
            LogicalOperator.EXACTLY,
        }:
            return self._compile_cardinality(
                operator,
                operands,
                expression.threshold or 0,
            )
        raise ValueError(f"Unsupported logical operator: {operator}")

    def _compile_cardinality(
        self,
        operator: LogicalOperator,
        operands: list[int],
        threshold: int,
    ) -> int:
        if operator == LogicalOperator.EXACTLY:
            at_least = self._compile_cardinality(
                LogicalOperator.AT_LEAST,
                operands,
                threshold,
            )
            at_most = self._compile_cardinality(
                LogicalOperator.AT_MOST,
                operands,
                threshold,
            )
            y = self._new_var()
            self.clauses.extend(
                [[-y, at_least], [-y, at_most], [y, -at_least, -at_most]]
            )
            return y

        n = len(operands)
        y = self._new_var()
        if operator == LogicalOperator.AT_LEAST:
            if threshold <= 0:
                self.clauses.append([y])
                return y
            if threshold > n:
                self.clauses.append([-y])
                return y
            # y -> at least k
            for subset in combinations(operands, n - threshold + 1):
                self.clauses.append([-y, *subset])
            # not y -> at most k-1
            for subset in combinations(operands, threshold):
                self.clauses.append([y, *[-x for x in subset]])
            return y

        if threshold >= n:
            self.clauses.append([y])
            return y
        if threshold < 0:
            self.clauses.append([-y])
            return y
        # y -> at most k
        for subset in combinations(operands, threshold + 1):
            self.clauses.append([-y, *[-x for x in subset]])
        # not y -> at least k+1
        for subset in combinations(operands, n - threshold):
            self.clauses.append([y, *subset])
        return y


def _simplify(
    clauses: list[list[int]],
    assignment: dict[int, bool],
) -> list[list[int]] | None:
    simplified: list[list[int]] = []
    for clause in clauses:
        new_clause: list[int] = []
        satisfied = False
        for literal in clause:
            var = abs(literal)
            value = assignment.get(var)
            if value is None:
                new_clause.append(literal)
                continue
            literal_true = value if literal > 0 else not value
            if literal_true:
                satisfied = True
                break
        if satisfied:
            continue
        if not new_clause:
            return None
        simplified.append(new_clause)
    return simplified


def _dpll(
    clauses: list[list[int]],
    assignment: dict[int, bool] | None = None,
) -> bool:
    assignment = dict(assignment or {})
    while True:
        simplified = _simplify(clauses, assignment)
        if simplified is None:
            return False
        if not simplified:
            return True

        unit = next(
            (clause[0] for clause in simplified if len(clause) == 1),
            None,
        )
        if unit is not None:
            assignment[abs(unit)] = unit > 0
            clauses = simplified
            continue

        polarity: dict[int, set[bool]] = {}
        for clause in simplified:
            for literal in clause:
                polarity.setdefault(abs(literal), set()).add(literal > 0)
        pure = next(
            (
                (var, next(iter(values)))
                for var, values in polarity.items()
                if len(values) == 1
            ),
            None,
        )
        if pure is not None:
            assignment[pure[0]] = pure[1]
            clauses = simplified
            continue

        clauses = simplified
        break

    chosen_clause = min(clauses, key=len)
    var = abs(chosen_clause[0])
    for value in (True, False):
        branch = dict(assignment)
        branch[var] = value
        if _dpll(clauses, branch):
            return True
    return False


def analyze_entailment(
    *,
    graphs: Iterable[MemoryGraph],
    queried_slot_ids: Iterable[str] = (),
    queried_slot_values: Iterable[tuple[str, bool]] | None = None,
) -> LogicEntailmentResult:
    """Classify candidate literals against the connected propositional KB.

    Legacy literal logic relations and graph-owned IMPLIES/REQUIRES edges compile
    directly to CNF when their semantic endpoints are bound to logic slots.
    Compound ASTs are Tseitin-compiled only when the stored logical structure
    actually contains compound Boolean/cardinality expressions.
    """
    graph_list = list(graphs)
    query_literals = (
        list(queried_slot_values)
        if queried_slot_values is not None
        else [(slot_id, True) for slot_id in queried_slot_ids]
    )
    query_literals = sorted(set(query_literals), key=lambda item: (item[0], item[1]))
    queried = {slot_id for slot_id, _ in query_literals}
    if not queried:
        return LogicEntailmentResult(
            status="undetermined",
            queried_slot_ids=(),
            relevant_rule_ids=(),
            relevant_assertion_ids=(),
            established_slot_ids=(),
            unresolved_slot_ids=(),
        )

    (
        slots,
        relevant_slots,
        literal_assertions,
        relations,
        graph_implications,
        compound_assertions,
        compound_rules,
    ) = _collect_relevant_component(graph_list, queried)
    compiler = _CNFCompiler(relevant_slots)

    for assertion in literal_assertions:
        compiler.clauses.append([compiler.compile_literal(assertion.literal)])

    for relation in relations:
        antecedent = compiler.compile_literal(relation.antecedent)
        consequent = compiler.compile_literal(relation.consequent)
        compiler.clauses.append([-antecedent, consequent])

    node_to_slot_bindings: dict[str, list[tuple[str, bool]]] = {}
    for slot_id in relevant_slots:
        slot = slots[slot_id]
        for binding in slot.bindings:
            node_to_slot_bindings.setdefault(binding.node_id, []).append(
                (slot_id, binding.value)
            )

    for edge in graph_implications:
        source_bindings = node_to_slot_bindings.get(edge.source_id, [])
        target_bindings = node_to_slot_bindings.get(edge.target_id, [])
        for source_slot, source_value in source_bindings:
            antecedent_var = compiler.slot_var[source_slot]
            antecedent = antecedent_var if source_value else -antecedent_var
            for target_slot, target_value in target_bindings:
                consequent_var = compiler.slot_var[target_slot]
                consequent = consequent_var if target_value else -consequent_var
                compiler.clauses.append([-antecedent, consequent])

    for assertion in compound_assertions:
        compiler.clauses.append([compiler.compile_expr(assertion.root)])

    for rule in compound_rules:
        condition = compiler.compile_expr(rule.condition)
        effect = compiler.compile_expr(rule.effect)
        compiler.clauses.append([-condition, effect])

    established: set[str] = set()
    unresolved: set[str] = set()
    for slot_id in relevant_slots:
        slot = slots[slot_id]
        established_bindings = [
            binding
            for binding in slot.bindings
            if _node_is_established(graph_list, binding.node_id)
        ]
        if not established_bindings:
            unresolved.add(slot_id)
        for binding in established_bindings:
            established.add(slot_id)
            var = compiler.slot_var[slot_id]
            compiler.clauses.append([var if binding.value else -var])

    # Bindings encode semantic identity including polarity. If one semantic node
    # binds to multiple slots, the corresponding variables are equal when the
    # binding values agree and negations of one another when they disagree.
    node_to_slots: dict[str, list[tuple[str, bool]]] = {}
    for slot_id in relevant_slots:
        for binding in slots[slot_id].bindings:
            node_to_slots.setdefault(binding.node_id, []).append(
                (slot_id, binding.value)
            )
    for slot_bindings in node_to_slots.values():
        if len(slot_bindings) < 2:
            continue
        anchor_slot, anchor_value = slot_bindings[0]
        anchor = compiler.slot_var[anchor_slot]
        for slot_id, value in slot_bindings[1:]:
            other = compiler.slot_var[slot_id]
            if value == anchor_value:
                compiler.clauses.extend([[-anchor, other], [anchor, -other]])
            else:
                compiler.clauses.extend([[-anchor, -other], [anchor, other]])

    base = compiler.clauses
    if not _dpll(base):
        status: LogicStatus = "inconsistent"
    else:
        entailed = False
        contradicted_count = 0
        considered = 0
        for slot_id, desired_value in query_literals:
            var = compiler.slot_var.get(slot_id)
            if var is None:
                continue
            considered += 1
            desired_literal = var if desired_value else -var
            opposite_literal = -desired_literal
            if not _dpll([*base, [opposite_literal]]):
                entailed = True
                break
            if not _dpll([*base, [desired_literal]]):
                contradicted_count += 1
        if entailed:
            status = "entailed"
        elif considered > 0 and contradicted_count == considered:
            status = "contradicted"
        else:
            status = "undetermined"

    return LogicEntailmentResult(
        status=status,
        queried_slot_ids=tuple(sorted(queried)),
        relevant_rule_ids=tuple(
            sorted(
                [
                    *(item.id for item in relations),
                    *(item.id for item in graph_implications),
                    *(item.id for item in compound_rules),
                ]
            )
        ),
        relevant_assertion_ids=tuple(
            sorted(
                [
                    *(item.id for item in literal_assertions),
                    *(item.id for item in compound_assertions),
                ]
            )
        ),
        established_slot_ids=tuple(sorted(established)),
        unresolved_slot_ids=tuple(sorted(unresolved)),
    )
