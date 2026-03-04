import asyncio
import datetime
import re

from cuga.backend.activity_tracker.tracker import ActivityTracker, Step

import os
from typing import Any, List, Optional, Literal, AsyncGenerator, Union
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from cuga.backend.browser_env.browser.gym_obs.http_stream_comm import ChromeExtensionCommunicatorProtocol
from cuga.backend.browser_env.tools.providers import BrowserToolImplProvider
from cuga.backend.cuga_graph.graph import DynamicAgentGraph
from cuga.backend.cuga_graph.nodes.browser.action_agent.tools.tools import setup_tools
from cuga.backend.cuga_graph.utils.event_porcessors.action_agent_event_processor import (
    ActionAgentEventProcessor,
)
from cuga.backend.cuga_graph.state.agent_state import AgentState, default_state
from cuga.backend.browser_env.browser.gym_env_async import BrowserEnvGymAsync
from cuga.backend.browser_env.browser.open_ended_async import OpenEndedTaskAsync
from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop, AgentLoopAnswer, StreamEvent
from cuga.config import get_app_name_from_url, settings, PACKAGE_ROOT
from loguru import logger
from langchain_core.messages import ToolCall

try:
    from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
except ImportError:
    try:
        from langfuse.callback.langchain import LangchainCallbackHandler as LangfuseCallbackHandler
    except ImportError:
        logger.warning("Langfuse is not installed, LangfuseCallbackHandler will be None")
        LangfuseCallbackHandler = None

tracker = ActivityTracker()


class ExperimentResult(BaseModel):
    score: float
    messages: List[AIMessage]
    answer: Optional[str] = ""
    number_of_actions: int = 0
    steps: Optional[List[Step]] = []


class AgentRunner:
    def __init__(self, browser_enabled=True, thread_id: str = "1"):
        self.browser_enabled = browser_enabled
        self.thread_id = thread_id
        self.env = None
        self.obs = None
        self.info = None
        self.agent_loop_obj = None
        pass

    @staticmethod
    async def process_event_async(
        tool_calls: Optional[List[ToolCall]],
        elements,
        page,
        tool_provider: BrowserToolImplProvider,
        session_id=None,
        page_data=None,
        communicator: ChromeExtensionCommunicatorProtocol | None = None,
    ) -> List[Any]:
        feedback = []
        if tool_calls:
            k = ActionAgentEventProcessor(page, tool_handlers=setup_tools())
            await k.process_action_agent_async(
                page,
                elements,
                tool_calls,
                tool_provider,
                session_id=session_id,
                page_data=page_data,
                communicator=communicator,
            )
            feedback = feedback + k.feedback_log
        # print("Retuned feedback before executor", feedback)
        return feedback

    async def initialize_webarena_env(self, task_id):
        from evaluation.tasks.task import GenericWebArenaTask

        self.env = BrowserEnvGymAsync(
            GenericWebArenaTask,
            headless=settings.eval_config.headless,
            resizeable_window=True,
            enable_playwright_tracing=True,
            feedback=[],
            task_kwargs={"task_id": task_id},
            enable_nocodeui_pu=True,
            pw_extra_args=[
                *settings.get("PLAYWRIGHT_ARGS", []),
                f"--disable-extensions-except={os.path.join(PACKAGE_ROOT, './cuga/backend/browser_env/browser/nocodeui_obs/prod')}",
                f"--load-extension={os.path.join(PACKAGE_ROOT, './cuga/backend/browser_env/browser/nocodeui_obs/prod')}",
            ],
        )

        self.obs, self.info = await self.env.reset()

    async def setup_page_info(self, state: AgentState, env):
        """Setup page URL, app name, and description from environment."""
        # Get URL and title
        state.url = env.page.url
        title = await env.page.title()
        url_app_name = get_app_name_from_url(state.url)
        # Sanitize title
        sanitized_title = re.sub(r'[^\w\s-]', '', title) if title else ""
        sanitized_title = re.sub(r'[-\s]+', '_', sanitized_title).strip('_').lower()
        # Create app name: url + sanitized title
        state.current_app = (
            f"{url_app_name}_{sanitized_title}" if sanitized_title else url_app_name or "unknown_app"
        )
        # Create description
        state.current_app_description = f"web application for '{title}' and url '{url_app_name}'"

    async def initialize_appworld_env(self):
        self.env = BrowserEnvGymAsync(
            OpenEndedTaskAsync,
            headless=settings.eval_config.headless,
            resizeable_window=True,
            task_kwargs={"start_url": "https://google.com"},
            interface_mode="none",
            enable_playwright_tracing=True,
            feedback=[],
            enable_nocodeui_pu=False,
        )

        self.obs, self.info = await self.env.reset()

    async def initialize_freemode_env(
        self, start_url, interface_mode: Literal['browser_only', 'chat_only', 'both'] = 'both'
    ):
        self.env = BrowserEnvGymAsync(
            OpenEndedTaskAsync,
            headless=False,
            interface_mode=interface_mode,
            feedback=[],
            timeout=15000,
            resizeable_window=True,
            task_kwargs={"start_url": start_url},
            tags_to_mark='all',
            enable_nocodeui_pu=False,
        )

        self.obs, self.info = await self.env.reset()

    async def browser_update_state(self, state: AgentState):
        if not self.browser_enabled:
            return
        await self.setup_page_info(state, self.env)

        state.url = self.env.get_url()
        state.current_app = get_app_name_from_url(state.url)
        pu_answer = await self.env.pu_processor.transform(transformer_params={"filter_visible_only": False})
        state.elements_as_string = pu_answer.string_representation
        state.focused_element_bid = pu_answer.focused_element_bid
        tracker.collect_image(pu_answer.img)
        state.read_page = pu_answer.page_content

    def get_current_state(self) -> AgentState:
        """
        Get the current agent state from the graph.

        Returns:
            AgentState: The current state from the graph

        Raises:
            RuntimeError: If agent_loop_obj is not initialized
        """
        if self.agent_loop_obj is None:
            raise RuntimeError("Agent loop not initialized. Call run_task_generic first.")

        return AgentState(
            **self.agent_loop_obj.graph.get_state({"configurable": {"thread_id": self.thread_id}}).values
        )

    async def run_task_generic(
        self,
        eval_mode=False,
        goal: str = None,
        sites: List[str] = None,
        current_datetime: Optional[str] = None,
        session_id: str = None,
    ) -> Optional[ExperimentResult]:
        langfuse_handler = None
        if settings.advanced_features.langfuse_tracing and LangfuseCallbackHandler is not None:
            langfuse_handler = LangfuseCallbackHandler()
            logger.debug("Langfuse tracing enabled for agent loop")

        agent = DynamicAgentGraph(None, langfuse_handler=langfuse_handler)
        await agent.build_graph()
        state: AgentState = default_state(
            page=self.env.page if self.env else None,
            observation=self.obs,
            goal=goal if goal else self.obs['goal'],
        )
        state.sites = sites
        await self.browser_update_state(state)

        self.agent_loop_obj = AgentLoop(
            thread_id=self.thread_id,
            langfuse_handler=langfuse_handler,
            graph=agent.graph,
            env_pointer=self.env,
            tracker=tracker,
            policy_system=agent.policy_system,
        )
        state.current_datetime = current_datetime if current_datetime else datetime.datetime.now().isoformat()
        state.pi = tracker.pi
        agent_response = await self.agent_loop_obj.run(state=state)
        reward = 0.0
        i = 0
        while True:
            if agent_response.has_tools:
                i += 1
                state = self.get_current_state()
                feedback = await AgentRunner.process_event_async(
                    state.messages[-1].tool_calls,
                    state.elements,
                    self.env.page,
                    self.env.tool_implementation_provider,
                    session_id=session_id,
                    tool_provider=self.env.tool_implementation_provider,
                )
                state.feedback = state.feedback + feedback
                if len(feedback) > 0 and feedback[-1]['status'] == "alert":
                    logger.warning(f"Adding to stm the alert {feedback[-1]['message']}")
                    state.stm_steps_history.append(
                        "Response of (ActionAgent): {}".format(feedback[-1]['message'])
                    )
                self.env.messages = state.messages
                obs, reward, terminated, truncated, info = await self.env.step("")
                if eval_mode and reward == 1.0 or len(tracker.steps) >= settings.evaluation.max_steps:
                    break
                await self.browser_update_state(state)
                self.agent_loop_obj.graph.update_state({"configurable": {"thread_id": self.thread_id}}, state)
                agent_response = await self.agent_loop_obj.run(state=None)
            elif agent_response.end:
                tracker.final_answer = agent_response.answer
                if self.env:
                    obs, reward, terminated, truncated, info = await self.env.step("")
                break
            else:
                raise Exception("Agent stopped but no tools or finish.")

        if eval_mode:
            if self.env.chat:
                await self.env.chat.add_message(
                    role="assistant",
                    msg="Final answer: {}".format(tracker.final_answer),
                )
            if len(tracker.steps) >= settings.evaluation.max_steps:
                tracker.final_answer = "N/A"
                obs, reward, terminated, truncated, info = await self.env.step("")
            if sites and len(sites) > 1:
                logger.debug("Sleep on finish if multi site")
                await asyncio.sleep(15)
                obs, reward, terminated, truncated, info = await self.env.step("")

            tracker.collect_score(score=reward)
            return ExperimentResult(
                score=reward,
                messages=state.messages,
                answer=tracker.final_answer,
                number_of_actions=tracker.actions_count,
                steps=tracker.steps,
            )
        else:
            return ExperimentResult(
                score=0.0,
                messages=state.messages,
                answer=tracker.final_answer,
                number_of_actions=tracker.actions_count,
                steps=tracker.steps,
            )

    async def run_task_generic_yield(
        self,
        eval_mode=False,
        goal: str = None,
        sites: List[str] = None,
        session_id: str = None,
        current_datetime: Optional[str] = None,
        chat_messages: List[AIMessage] = None,
    ) -> AsyncGenerator[Union[ExperimentResult, StreamEvent], None]:
        logger.debug(
            "Initiated agent with number of chat messages: {}".format(
                len(chat_messages) if chat_messages else 0
            )
        )
        langfuse_handler = None
        if settings.advanced_features.langfuse_tracing and LangfuseCallbackHandler is not None:
            langfuse_handler = LangfuseCallbackHandler()
            logger.debug("Langfuse tracing enabled for agent loop")

        agent = DynamicAgentGraph(None, langfuse_handler=langfuse_handler)
        await agent.build_graph()
        state: AgentState = default_state(
            page=self.env.page if self.env else None,
            observation=self.obs,
            goal=goal if goal else self.obs['goal'],
            chat_messages=chat_messages if chat_messages else [],
        )
        state.sites = sites
        await self.browser_update_state(state)

        self.agent_loop_obj = AgentLoop(
            thread_id=self.thread_id,
            langfuse_handler=langfuse_handler,
            graph=agent.graph,
            tracker=tracker,
            env_pointer=self.env,
            policy_system=agent.policy_system,
        )
        state.current_datetime = current_datetime if current_datetime else datetime.datetime.now().isoformat()
        state.pi = tracker.pi
        reward = 0.0
        i = 0
        first_time = True
        event = None
        while True:
            agent_response = self.agent_loop_obj.run_stream(state=state if first_time else None)
            first_time = False
            final_event = None
            async for event in agent_response:
                final_event = event  # Keep track of the last event

                if isinstance(event, AgentLoopAnswer):
                    if event.has_tools:
                        i += 1
                        state = self.get_current_state()
                        feedback = await AgentRunner.process_event_async(
                            state.messages[-1].tool_calls,
                            state.elements,
                            self.env.page,
                            self.env.tool_implementation_provider,
                            session_id=session_id,
                        )
                        state.feedback = state.feedback + feedback
                        if len(feedback) > 0 and feedback[-1]['status'] == "alert":
                            logger.warning(f"Adding to stm the alert {feedback[-1]['message']}")
                            state.stm_steps_history.append(
                                "Response of (ActionAgent): {}".format(feedback[-1]['message'])
                            )
                        self.env.messages = state.messages
                        obs, reward, terminated, truncated, info = await self.env.step("")
                        if eval_mode and reward == 1.0 or len(tracker.steps) >= settings.evaluation.max_steps:
                            break  # Break to handle final result outside the loop
                        await self.browser_update_state(state)
                        self.agent_loop_obj.graph.update_state(
                            {"configurable": {"thread_id": self.thread_id}}, state
                        )
                        # Break out of the async for loop to restart with new agent_response
                        break
                    elif event.end:
                        tracker.final_answer = event.answer
                        if self.env:
                            obs, reward, terminated, truncated, info = await self.env.step("")
                        yield ExperimentResult(
                            score=0.0,
                            messages=state.messages,
                            answer=tracker.final_answer,
                            number_of_actions=tracker.actions_count,
                        )
                        return  # Exit the entire function
                    else:
                        raise Exception("Agent stopped but no tools or finish.")
                else:
                    yield StreamEvent.parse(event)

            # Handle the case where we broke out due to max_steps or reward == 1.0
            if eval_mode and (reward == 1.0 or len(tracker.steps) >= settings.evaluation.max_steps):
                break

        # Handle final result outside the loop (matching your original structure)
        if final_event and isinstance(final_event, AgentLoopAnswer):
            tracker.final_answer = final_event.answer
            if self.env:
                obs, reward, terminated, truncated, info = await self.env.step("")
            yield ExperimentResult(
                score=0.0,
                messages=state.messages,
                answer=tracker.final_answer,
                number_of_actions=tracker.actions_count,
            )


async def main():
    ar = AgentRunner(browser_enabled=False)
    await ar.initialize_appworld_env()
    await ar.run_task_generic(
        eval_mode=False,
        goal="Get my accounts top two accounts by revenue from digital sales",
    )


if __name__ == '__main__':
    asyncio.run(main())
