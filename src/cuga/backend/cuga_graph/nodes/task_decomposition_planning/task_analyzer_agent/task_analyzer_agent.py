from langchain_core.messages import AIMessage
from opentelemetry import trace as otel_trace
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.prompts.load_prompt import (
    AnalyzeTaskOutput,
)
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.tasks.app_matcher import (
    match_apps_for_intent,
)
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.tasks.classify_task import (
    classify_task,
    Attributes,
)
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.tasks.navigation_paths_task import (
    navigation_paths_task,
    Approaches,
)
from cuga.backend.cuga_graph.nodes.task_decomposition_planning.task_analyzer_agent.tasks.paraphrase import (
    paraphrase_task,
)
from cuga.backend.llm.models import LLMManager
from cuga.config import settings
from loguru import logger
from cuga.backend.activity_tracker.tracker import ActivityTracker

tracker = ActivityTracker()

llm_manager = LLMManager()


class TaskAnalyzerAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self.name = "TaskAnalyzerAgent"
        # enable_format = settings.agent.task_decomposition.model.enable_format
        self.classify_task = classify_task(settings.agent.task_decomposition.model)
        self.navigation_paths_task = navigation_paths_task(settings.agent.task_decomposition.model)
        self.match_apps_task = match_apps_for_intent(settings.agent.task_decomposition.model)
        self.paraphrase_task = paraphrase_task(settings.agent.task_decomposition.model)

    async def run(self, input_variables: AgentState) -> AIMessage:
        apps_maps = {
            "gitlab": "Gitlab community",
            "shopping_admin": "Magento shopping admin",
            "reddit": "Postmill or reddit",
            "shopping": "Shopping",
            "map": "OpenStreetsMap",
            "maps": "OpenStreetsMap",
            "unknown": "unknown",
        }
        current_app_name = apps_maps.get(input_variables.current_app, "unknown")
        inp = input_variables.model_dump()
        inp.update({"app_name": current_app_name, "task": input_variables.input})
        attrs: Attributes = Attributes(
            thoughts=[],
            performs_update=False,
            requires_memory=False,
            requires_loop=False,
            requires_location_search=False,
        )
        if settings.advanced_features.benchmark == "webarena":
            attrs: Attributes = await self.classify_task.ainvoke(inp)
        task_analyzer_output = AnalyzeTaskOutput(attrs=attrs)
        read_only_deep_analysis = bool(
            attrs
            and not attrs.performs_update
            and input_variables.current_app in ['gitlab', 'shopping_admin']
        )
        otel_trace.get_current_span().set_attribute(
            "cuga.task_analyzer.read_only_deep_analysis", read_only_deep_analysis
        )
        if read_only_deep_analysis:
            if settings.advanced_features.use_paraphrase:
                task_analyzer_output.paraphrased_intent = (
                    await self.paraphrase_task.with_config(configurable={"llm_temperature": 0.1}).ainvoke(inp)
                ).rephrased_intent
                logger.debug(f"Paraphrased intent: '{task_analyzer_output.paraphrased_intent}'")
                inp['input'] = task_analyzer_output.paraphrased_intent
            apps_maps = {"gitlab": "Gitlab community", "shopping_admin": "Magento shopping admin"}
            current_app_name = apps_maps[input_variables.current_app]
            inp = input_variables.model_dump()
            inp.update({"app_name": current_app_name, "task": input_variables.input})
            logger.debug("Intent is read only")
            approaches: Approaches = await self.navigation_paths_task.with_config(
                configurable={"llm_temperature": 0.3}
            ).ainvoke(inp)
            # Sorted approaches by not extensive in pagination.
            task_analyzer_output.navigation_paths = approaches
            task_analyzer_output.navigation_paths.approaches = sorted(
                task_analyzer_output.navigation_paths.approaches, key=lambda obj: obj.extensive_pagination
            )
            logger.debug(
                "Navigation from knowledge: \n{}".format(
                    '\n'.join([d.approach for d in task_analyzer_output.navigation_paths.approaches])
                )
            )
        return AIMessage(content=task_analyzer_output.model_dump_json())

    @staticmethod
    def create():
        return TaskAnalyzerAgent()
