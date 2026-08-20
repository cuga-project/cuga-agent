import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.tools import tool

from cuga import CugaSupervisor
from cuga.backend.cuga_graph.nodes.cuga_supervisor.delegation import create_agent_delegation_func
from cuga.backend.cuga_graph.nodes.cuga_supervisor.execution_context import (
    SUPERVISOR_EXEC_KEY,
    SupervisorExecutionContext,
)
from cuga.backend.cuga_graph.nodes.cuga_supervisor.supervisor_graph_adapter import SupervisorGraphAdapter
from cuga.backend.cuga_graph.policy.tests.helpers import setup_langfuse_tracing
from cuga.backend.cuga_graph.state.agent_state import VariablesManager

try:
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events import EventQueue
    from a2a.utils.message import new_agent_text_message

    HAS_A2A_SDK = True
except ImportError:
    HAS_A2A_SDK = False

try:
    from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill

    HAS_A2A_HTTP_SERVER = True
except ImportError:
    HAS_A2A_HTTP_SERVER = False


if HAS_A2A_SDK:

    class RemoteAnswerAgent:
        async def invoke(self, task: str) -> str:
            return "Success! The remote agent has completed the calculation: 42"

    class RemoteAnswerAgentExecutor(AgentExecutor):
        def __init__(self):
            self.agent = RemoteAnswerAgent()

        async def execute(
            self,
            context: RequestContext,
            event_queue: EventQueue,
        ) -> None:
            task_text = context.get_user_input()
            result = await self.agent.invoke(task_text)
            await event_queue.enqueue_event(new_agent_text_message(result))

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            raise NotImplementedError("cancel not supported")


# --- Test Tools for Multi-Agent Test ---
@tool
def get_user_id(name: str) -> str:
    """Get the internal user ID for a given name.
    Args:
        name: Name of the user
    """
    if name.lower() == "alice":
        return "user_alice_99"
    return "unknown_user"


@tool
def get_user_account_value(user_id: str) -> int:
    """Get the account value for a specific user ID.
    Args:
        user_id: The unique user ID
    """
    if user_id == "user_alice_99":
        return 1500
    return 0


@tool
def process_special_bonus(user_id: str, amount: int) -> str:
    """Process a special bonus for a user based on their account value.
    Args:
        user_id: The unique user ID
        amount: The account value to base the bonus on
    """
    bonus = amount * 0.1
    return f"Processed bonus of {bonus} for {user_id}"


class _FakeCugaAgent:
    pass


def _empty_delegation_state():
    return SimpleNamespace(
        selected_agents=[],
        agent_results={},
        agent_variables={},
        agent_chat_messages={},
        supervisor_metadata={},
        metrics={},
    )


def _tool_agent(handler):
    agent = _FakeCugaAgent()
    agent.invoke = AsyncMock(side_effect=handler)
    return agent


class TestSupervisorAdvanced:
    """Advanced tests for CugaSupervisor coordination and A2A."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_supervisor_coordination_with_variable_passing(self):
        """Alice lookup → account value → bonus, via the delegation payload not LLM text."""

        async def find_user(task, **kwargs):
            user_id = get_user_id.invoke({"name": "Alice"})
            return SimpleNamespace(answer=f"Found {user_id}", variables={"user_id": user_id})

        async def get_account(task, **kwargs):
            user_id = (kwargs.get("variables") or {}).get("user_id", "")
            value = get_user_account_value.invoke({"user_id": user_id})
            return SimpleNamespace(answer=str(value), variables={"account_value": value})

        async def process_bonus(task, **kwargs):
            passed = kwargs.get("variables") or {}
            result = process_special_bonus.invoke(
                {"user_id": passed.get("user_id", ""), "amount": passed.get("account_value") or 0}
            )
            return SimpleNamespace(answer=result, variables={})

        user_finder = _tool_agent(find_user)
        account_manager = _tool_agent(get_account)
        bonus_processor = _tool_agent(process_bonus)

        adapter = SupervisorGraphAdapter(agents={})
        state = _empty_delegation_state()
        with patch("cuga.sdk.CugaAgent", _FakeCugaAgent):
            namespace = {
                SUPERVISOR_EXEC_KEY: SupervisorExecutionContext(
                    state=state, variable_manager=VariablesManager()
                ),
                "delegate_user_finder": create_agent_delegation_func(adapter, "user_finder", user_finder),
                "delegate_account_manager": create_agent_delegation_func(
                    adapter, "account_manager", account_manager
                ),
                "delegate_bonus_processor": create_agent_delegation_func(
                    adapter, "bonus_processor", bonus_processor
                ),
            }
            exec(
                "async def _run():\n"
                "    await delegate_user_finder('Find the user ID for Alice')\n"
                "    await delegate_account_manager('Get her account value', variables=['user_id'])\n"
                "    return await delegate_bonus_processor("
                "'Process a special bonus', variables=['user_id', 'account_value'])\n",
                namespace,
                namespace,
            )
            answer = await namespace["_run"]()

        assert account_manager.invoke.await_args.kwargs["variables"] == {"user_id": "user_alice_99"}
        assert bonus_processor.invoke.await_args.kwargs["variables"] == {
            "user_id": "user_alice_99",
            "account_value": 1500,
        }
        assert answer == "Processed bonus of 150.0 for user_alice_99"
        assert state.selected_agents == ["user_finder", "account_manager", "bonus_processor"]
        assert state.agent_variables["user_finder"]["user_id"] == "user_alice_99"

    @pytest.mark.e2e
    @pytest.mark.asyncio
    @pytest.mark.skipif(not HAS_A2A_SDK, reason="a2a-sdk not installed")
    @pytest.mark.skipif(not HAS_A2A_HTTP_SERVER, reason="a2a-sdk[http-server] not installed")
    async def test_supervisor_a2a_connection(self):
        """
        Test T2: Supervisor connects to a real local A2A agent via a2a-sdk:
        fetches agent card from /.well-known/agent-card.json, sends task with
        A2AClient.send_message (task only, no variables).
        """
        import uvicorn

        A2A_TEST_PORT = 18765
        endpoint = f"http://127.0.0.1:{A2A_TEST_PORT}"
        handler = setup_langfuse_tracing()
        callbacks = [handler] if handler else []

        executor = RemoteAnswerAgentExecutor()
        task_store = InMemoryTaskStore()
        request_handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=task_store,
        )
        agent_card = AgentCard(
            name="RemoteAnswerAgent",
            description="Returns the answer to everything (42).",
            url=endpoint,
            version="1.0.0",
            capabilities=AgentCapabilities(),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[
                AgentSkill(
                    id="answer",
                    name="Answer",
                    description="Returns the answer to everything (42).",
                    tags=[],
                )
            ],
        )
        starlette_app = A2AStarletteApplication(
            agent_card=agent_card,
            http_handler=request_handler,
        )
        app = starlette_app.build()
        config = uvicorn.Config(app, host="127.0.0.1", port=A2A_TEST_PORT, log_level="warning")
        server = uvicorn.Server(config)
        serve_task = asyncio.create_task(server.serve())
        await asyncio.sleep(1.0)
        try:
            external_agent_config = {
                "name": "remote_assistant",
                "type": "external",
                "description": "A remote agent reachable via A2A protocol",
                "config": {"a2a_protocol": {"endpoint": endpoint, "transport": "http"}},
            }
            supervisor = CugaSupervisor(agents={"remote_agent": external_agent_config}, callbacks=callbacks)
            task = "Ask the remote agent for the answer to everything"
            result = await supervisor.invoke(task)

            if handler and hasattr(handler, "get_trace_url"):
                print(f"\n  📊 Langfuse trace: {handler.get_trace_url()}")

            assert result is not None
            assert "42" in result.answer
        finally:
            server.should_exit = True
            await serve_task
