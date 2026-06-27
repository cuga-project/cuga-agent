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
    """Tests for PlanUpfrontDelegation Pydantic model."""

    def test_create_delegation_with_required_fields(self):
        """Create delegation with only agent_name and task."""
        d = PlanUpfrontDelegation(agent_name="crm_agent", task="Get customers")
        assert d.agent_name == "crm_agent"
        assert d.task == "Get customers"
        assert d.variables == []

    def test_create_delegation_with_variables(self):
        """Create delegation with optional variables list."""
        d = PlanUpfrontDelegation(
            agent_name="email_agent",
            task="Send email",
            variables=["customer_data"],
        )
        assert d.variables == ["customer_data"]

    def test_delegation_requires_agent_name(self):
        """Delegation without agent_name raises ValidationError."""
        with pytest.raises(ValidationError):
            PlanUpfrontDelegation(task="task")

    def test_delegation_requires_task(self):
        """Delegation without task raises ValidationError."""
        with pytest.raises(ValidationError):
            PlanUpfrontDelegation(agent_name="agent")


class TestPlanUpfrontPlan:
    """Tests for PlanUpfrontPlan Pydantic model."""

    def test_create_plan_defaults(self):
        """Plan uses default strategy 'sequential' and empty delegations."""
        p = PlanUpfrontPlan()
        assert p.strategy == "sequential"
        assert p.delegations == []
        assert p.reasoning is None

    def test_create_plan_with_delegations(self):
        """Plan accepts delegations list and explicit strategy."""
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
        """Plan rejects strategy not in sequential or parallel."""
        with pytest.raises(ValidationError):
            PlanUpfrontPlan(strategy="invalid")

    def test_plan_serialize_roundtrip(self):
        """Plan model_dump and reconstruction preserves all fields."""
        d = PlanUpfrontDelegation(agent_name="a", task="t")
        p = PlanUpfrontPlan(strategy="sequential", delegations=[d])
        data = p.model_dump()
        restored = PlanUpfrontPlan(**data)
        assert restored.strategy == "sequential"
        assert restored.delegations[0].agent_name == "a"


class TestCugaSupervisorState:
    """Tests for CugaSupervisorState with new plan_upfront fields."""

    def test_default_supervisor_mode(self):
        """Default supervisor_mode is conversational."""
        state = CugaSupervisorState(input="hello", url="")
        assert state.supervisor_mode == "conversational"

    def test_explicit_conversational_mode(self):
        """Setting supervisor_mode explicitly to conversational."""
        state = CugaSupervisorState(
            input="hello",
            url="",
            supervisor_mode="conversational",
        )
        assert state.supervisor_mode == "conversational"

    def test_explicit_plan_upfront_mode(self):
        """Setting supervisor_mode to plan_upfront is accepted."""
        state = CugaSupervisorState(
            input="hello",
            url="",
            supervisor_mode="plan_upfront",
        )
        assert state.supervisor_mode == "plan_upfront"

    def test_invalid_mode_rejected(self):
        """Legacy delegation value raises ValidationError."""
        with pytest.raises(ValidationError):
            CugaSupervisorState(input="hello", url="", supervisor_mode="delegation")

    def test_plan_upfront_plan_field_default(self):
        """plan_upfront_plan defaults to None."""
        state = CugaSupervisorState(input="hello", url="")
        assert state.plan_upfront_plan is None

    def test_plan_upfront_plan_field_set(self):
        """plan_upfront_plan can be set via dict or PlanUpfrontPlan."""
        d = PlanUpfrontDelegation(agent_name="a", task="t")
        plan = PlanUpfrontPlan(strategy="sequential", delegations=[d])
        state = CugaSupervisorState(
            input="hello",
            url="",
            supervisor_mode="plan_upfront",
            plan_upfront_plan=plan.model_dump(),
        )
        assert state.plan_upfront_plan is not None
        assert state.plan_upfront_plan.strategy == "sequential"

    def test_aggregated_results_default(self):
        """aggregated_results defaults to None."""
        state = CugaSupervisorState(input="hello", url="")
        assert state.aggregated_results is None

    def test_aggregated_results_set(self):
        """aggregated_results can be set with a string value."""
        state = CugaSupervisorState(
            input="hello",
            url="",
            aggregated_results="Agent results here",
        )
        assert state.aggregated_results == "Agent results here"

    def test_synthesized_response_default(self):
        """synthesized_response defaults to None."""
        state = CugaSupervisorState(input="hello", url="")
        assert state.synthesized_response is None

    def test_synthesized_response_set(self):
        """synthesized_response can be set with a string value."""
        state = CugaSupervisorState(
            input="hello",
            url="",
            synthesized_response="Final answer",
        )
        assert state.synthesized_response == "Final answer"

    def test_all_new_fields_in_model_dump(self):
        """All plan_upfront fields appear in model_dump output."""
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
