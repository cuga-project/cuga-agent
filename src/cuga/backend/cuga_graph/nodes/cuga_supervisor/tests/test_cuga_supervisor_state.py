"""Tests for CugaSupervisorState, PlanUpfrontPlan, and PlanUpfrontDelegation models."""

from langchain_core.messages import HumanMessage
from pydantic import ValidationError
import pytest

from cuga.backend.cuga_graph.nodes.cuga_supervisor.cuga_supervisor_state import (
    CugaSupervisorState,
    PlanUpfrontPlan,
    PlanUpfrontDelegation,
)


class TestPlanUpfrontDelegation:
    def test_create_delegation_with_required_fields(self):
        d = PlanUpfrontDelegation(agent_name="crm_agent", task="Get customers")
        assert d.agent_name == "crm_agent"
        assert d.task == "Get customers"
        assert d.variables == []

    def test_create_delegation_with_variables(self):
        d = PlanUpfrontDelegation(
            agent_name="email_agent",
            task="Send email",
            variables=["customer_data"],
        )
        assert d.variables == ["customer_data"]

    def test_delegation_requires_agent_name(self):
        with pytest.raises(ValidationError):
            PlanUpfrontDelegation(task="task")

    def test_delegation_requires_task(self):
        with pytest.raises(ValidationError):
            PlanUpfrontDelegation(agent_name="agent")


class TestPlanUpfrontPlan:
    def test_create_plan_defaults(self):
        p = PlanUpfrontPlan()
        assert p.strategy == "sequential"
        assert p.delegations == []
        assert p.reasoning is None

    def test_create_plan_with_delegations(self):
        d = PlanUpfrontDelegation(agent_name="crm_agent", task="Get data")
        p = PlanUpfrontPlan(
            strategy="parallel",
            delegations=[d],
            reasoning="Run in parallel",
        )
        assert p.strategy == "parallel"
        assert len(p.delegations) == 1
        assert p.delegations[0].agent_name == "crm_agent"
        assert p.reasoning == "Run in parallel"

    def test_plan_rejects_invalid_strategy(self):
        with pytest.raises(ValidationError):
            PlanUpfrontPlan(strategy="invalid")

    def test_plan_serialize_roundtrip(self):
        d = PlanUpfrontDelegation(agent_name="a", task="t")
        p = PlanUpfrontPlan(strategy="sequential", delegations=[d])
        data = p.model_dump()
        restored = PlanUpfrontPlan(**data)
        assert restored.strategy == "sequential"
        assert restored.delegations[0].agent_name == "a"


class TestCugaSupervisorState:
    def test_default_supervisor_mode(self):
        state = CugaSupervisorState(input="hello", url="")
        assert state.supervisor_mode == "conversational"

    def test_explicit_conversational_mode(self):
        state = CugaSupervisorState(
            input="hello",
            url="",
            supervisor_mode="conversational",
        )
        assert state.supervisor_mode == "conversational"

    def test_explicit_plan_upfront_mode(self):
        state = CugaSupervisorState(
            input="hello",
            url="",
            supervisor_mode="plan_upfront",
        )
        assert state.supervisor_mode == "plan_upfront"

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValidationError):
            CugaSupervisorState(input="hello", url="", supervisor_mode="delegation")

    def test_plan_upfront_plan_field_default(self):
        state = CugaSupervisorState(input="hello", url="")
        assert state.plan_upfront_plan is None

    def test_plan_upfront_plan_field_set(self):
        d = PlanUpfrontDelegation(agent_name="a", task="t")
        plan = PlanUpfrontPlan(strategy="sequential", delegations=[d])
        state = CugaSupervisorState(
            input="hello",
            url="",
            supervisor_mode="plan_upfront",
            plan_upfront_plan=plan.model_dump(),
        )
        assert state.plan_upfront_plan is not None
        assert state.plan_upfront_plan["strategy"] == "sequential"

    def test_aggregated_results_default(self):
        state = CugaSupervisorState(input="hello", url="")
        assert state.aggregated_results is None

    def test_aggregated_results_set(self):
        state = CugaSupervisorState(
            input="hello",
            url="",
            aggregated_results="Agent results here",
        )
        assert state.aggregated_results == "Agent results here"

    def test_synthesized_response_default(self):
        state = CugaSupervisorState(input="hello", url="")
        assert state.synthesized_response is None

    def test_synthesized_response_set(self):
        state = CugaSupervisorState(
            input="hello",
            url="",
            synthesized_response="Final answer",
        )
        assert state.synthesized_response == "Final answer"

    def test_all_new_fields_in_model_dump(self):
        state = CugaSupervisorState(
            input="test",
            url="",
            supervisor_chat_messages=[HumanMessage(content="hi")],
        )
        data = state.model_dump()
        assert "supervisor_mode" in data
        assert "plan_upfront_plan" in data
        assert "aggregated_results" in data
        assert "synthesized_response" in data
