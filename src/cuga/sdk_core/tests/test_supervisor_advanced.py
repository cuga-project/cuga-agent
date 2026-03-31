import asyncio
import pytest
from langchain_core.tools import tool

from cuga import CugaAgent, CugaSupervisor
from cuga.backend.cuga_graph.policy.tests.helpers import setup_langfuse_tracing

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


class TestSupervisorAdvanced:
    """Advanced tests for CugaSupervisor coordination and A2A."""

    @pytest.mark.asyncio
    async def test_supervisor_coordination_with_variable_passing(self):
        """
        Test T1: Supervisor with 3 sub-agents, tools, and variable passing.
        1. Agent A gets user_id.
        2. Agent B gets account value using user_id.
        3. Agent C processes bonus using user_id and account value.
        """
        handler = setup_langfuse_tracing()
        callbacks = [] if handler else []

        agent_a = CugaAgent(tools=[get_user_id], callbacks=callbacks)
        agent_a.description = "Agent that finds user IDs"

        agent_b = CugaAgent(tools=[get_user_account_value], callbacks=callbacks)
        agent_b.description = "Agent that finds account values"

        agent_c = CugaAgent(tools=[process_special_bonus], callbacks=callbacks)
        agent_c.description = "Agent that processes bonuses"
        callbacks_supervisor = [handler] if handler else []
        supervisor = CugaSupervisor(
            agents={"user_finder": agent_a, "account_manager": agent_b, "bonus_processor": agent_c},
            callbacks=callbacks_supervisor,
        )

        # The task requires sequential logic and variable passing
        task = "Find the user ID for Alice, then get her account value, and finally process a special bonus for her."

        result = await supervisor.invoke(task)

        if handler and hasattr(handler, 'get_trace_url'):
            print(f"\n  📊 Langfuse trace: {handler.get_trace_url()}")

        assert result is not None
        assert result.error is None
        # Verify the supervisor coordinated all agents and passed variables.
        # The exact bonus formatting varies (150, 150.0, $150, etc.),
        # so we check for the user ID and that a bonus was mentioned.
        assert "user_alice_99" in result.answer
        answer_lower = result.answer.lower()
        assert "bonus" in answer_lower

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
