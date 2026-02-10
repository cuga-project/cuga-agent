from typing import Any, Optional, Dict, List

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.language_models import BaseChatModel
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.nodes.shared.base_agent import BaseAgent
from cuga.backend.cuga_graph.state.agent_state import AgentState
from cuga.backend.cuga_graph.nodes.api.api_planner_agent.prompts.load_prompt import (
    APIPlannerOutput,
    APIPlannerOutputLite,
    APIPlannerOutputNoHITL,
    APIPlannerOutputLiteNoHITL,
)
from cuga.backend.llm.models import LLMManager
from cuga.backend.llm.utils.helpers import load_prompt_simple
from cuga.config import settings
from cuga.configurations.instructions_manager import InstructionsManager

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None

try:
    from langchain_groq import ChatGroq
except ImportError:
    ChatGroq = None

try:
    from langchain_ibm import ChatWatsonx
except ImportError:
    ChatWatsonx = None

instructions_manager = InstructionsManager()
tracker = ActivityTracker()
llm_manager = LLMManager()


def _get_model_identifier(llm: BaseChatModel) -> Optional[str]:
    """
    Safely extract model identifier from different LLM classes.

    Supports:
    - ChatWatsonx: uses model_id attribute
    - ChatOpenAI: uses model_name attribute
    - ChatGroq: uses model attribute
    - Other BaseChatModel subclasses: tries model_id, model_name, model in that order

    Args:
        llm: The language model instance

    Returns:
        Model identifier string or None if not found
    """
    if ChatWatsonx is not None and isinstance(llm, ChatWatsonx):
        return getattr(llm, 'model_id', None)
    elif ChatOpenAI is not None and isinstance(llm, ChatOpenAI):
        return getattr(llm, 'model_name', None)
    elif ChatGroq is not None and isinstance(llm, ChatGroq):
        return getattr(llm, 'model', None)
    else:
        # Try common attribute names in order of preference
        for attr in ['model_id', 'model_name', 'model']:
            if hasattr(llm, attr):
                value = getattr(llm, attr)
                if value:
                    return str(value)
    return None


class APIPlannerAgent(BaseAgent):
    def __init__(self, prompt_template: ChatPromptTemplate, llm: BaseChatModel, tools: Any = None):
        super().__init__()
        self.name = "APIPlannerAgent"

        model_id = _get_model_identifier(llm)
        self.thoughts_enabled = not (model_id and "oss" in model_id) and settings.features.thoughts

        if settings.advanced_features.api_planner_hitl:
            schema = APIPlannerOutputLite if not self.thoughts_enabled else APIPlannerOutput
        else:
            schema = APIPlannerOutputLiteNoHITL if not self.thoughts_enabled else APIPlannerOutputNoHITL

        self.chain = BaseAgent.get_chain(prompt_template=prompt_template, llm=llm, schema=schema)

    @staticmethod
    def _safe_text(value: Any) -> str:
        """Convert optional values to trimmed strings."""
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _escape_inline_code(value: str) -> str:
        """Keep markdown inline-code blocks well-formed."""
        return value.replace("`", "'")

    @staticmethod
    def _extract_structured_facts(preferences: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Normalize preference payload into structured facts for stronger prompt grounding.

        Returns list of dictionaries with keys: category, key, value, content.
        """
        if not preferences:
            return []

        facts: List[Dict[str, str]] = []
        first_value = next(iter(preferences.values()), None)

        if isinstance(first_value, list):
            for category, category_facts in preferences.items():
                if not isinstance(category_facts, list):
                    continue
                category_name = APIPlannerAgent._safe_text(category) or "misc"
                for fact in category_facts:
                    if isinstance(fact, dict):
                        key = APIPlannerAgent._safe_text(fact.get("key"))
                        value = APIPlannerAgent._safe_text(fact.get("value"))
                        content = APIPlannerAgent._safe_text(fact.get("content"))
                    else:
                        key = ""
                        value = ""
                        content = APIPlannerAgent._safe_text(fact)

                    if not any([key, value, content]):
                        continue

                    facts.append(
                        {
                            "category": category_name,
                            "key": key,
                            "value": value,
                            "content": content,
                        }
                    )
        else:
            for fact_id, content in preferences.items():
                text = APIPlannerAgent._safe_text(content)
                if not text:
                    continue
                facts.append(
                    {
                        "category": "legacy",
                        "key": APIPlannerAgent._safe_text(fact_id),
                        "value": "",
                        "content": text,
                    }
                )

        # Deduplicate while preserving order
        deduped_facts: List[Dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for fact in facts:
            signature = (fact["category"], fact["key"], fact["value"], fact["content"])
            if signature in seen:
                continue
            seen.add(signature)
            deduped_facts.append(fact)
        return deduped_facts

    @staticmethod
    def _format_fact_pointer(fact: Dict[str, str]) -> str:
        """Format category/key path for fact reference."""
        category = APIPlannerAgent._safe_text(fact.get("category")) or "misc"
        key = APIPlannerAgent._safe_text(fact.get("key"))
        if key:
            return f"{category}.{key}"
        return category

    @staticmethod
    def _format_preferences_for_decision_context(preferences: Dict[str, Any]) -> str:
        """Create compact, structured context facts for the user prompt section."""
        facts = APIPlannerAgent._extract_structured_facts(preferences)
        if not facts:
            return ""

        lines = [
            "Use these persistent user facts to drive planning decisions and query scoping.",
            "Prefer specific filters from these facts over broad listing operations when possible.",
            "",
            "Structured facts:",
        ]
        for fact in facts[:25]:
            pointer = APIPlannerAgent._escape_inline_code(APIPlannerAgent._format_fact_pointer(fact))
            value = APIPlannerAgent._safe_text(fact.get("value"))
            content = APIPlannerAgent._safe_text(fact.get("content"))
            if value:
                value = APIPlannerAgent._escape_inline_code(value)
                lines.append(f"- `{pointer}` = `{value}`")
            elif content:
                lines.append(f"- `{pointer}`: {content}")
        return "\n".join(lines)

    @staticmethod
    def _format_preferences_for_prompt(preferences: Dict[str, Any]) -> str:
        """Format user preferences for prompt injection.
        
        Supports both legacy format (fact_id -> content string) and new categorized format
        (category -> list of fact dicts).

        Args:
            preferences: Dictionary in one of two formats:
                - Legacy: {fact_id: content_string}
                - Categorized: {category: [{"content": "...", "key": "...", "value": "..."}]}

        Returns:
            Formatted string for prompt injection organized by category
        """
        if not preferences:
            return ""

        structured_facts = APIPlannerAgent._extract_structured_facts(preferences)
        lines = ["\n## User Preferences"]
        lines.extend(
            [
                "",
                "### Personalization Constraints (High Priority)",
                "- Treat these memory facts as persistent user context that should influence planning decisions.",
                (
                    "- Use memory facts to resolve ambiguous references (e.g., pronouns, omitted entities, "
                    "or possessive terms like 'my') before broad operations."
                ),
                (
                    "- When facts provide concrete filter values, prefer scoped API shortlisting and "
                    "scoped queries over global listing."
                ),
                "- In thoughts, explicitly cite which memory fact(s) were used to choose the next action.",
            ]
        )

        if structured_facts:
            lines.append("")
            lines.append("### Structured Facts For Decision Making")
            for fact in structured_facts[:25]:
                pointer = APIPlannerAgent._escape_inline_code(APIPlannerAgent._format_fact_pointer(fact))
                value = APIPlannerAgent._safe_text(fact.get("value"))
                content = APIPlannerAgent._safe_text(fact.get("content"))
                if value:
                    value = APIPlannerAgent._escape_inline_code(value)
                    lines.append(f"- `{pointer}` = `{value}`")
                elif content:
                    lines.append(f"- `{pointer}`: {content}")

        # Detect format by checking first value
        first_value = next(iter(preferences.values()), None)
        
        if isinstance(first_value, list):
            # New categorized format
            for category, facts in preferences.items():
                if not facts:
                    continue
                    
                # Format category name: "food" -> "Food", "personal_details" -> "Personal Details"
                category_display = category.replace('_', ' ').title()
                lines.append(f"\n### {category_display}")
                
                for fact in facts:
                    content = fact.get('content', '') if isinstance(fact, dict) else str(fact)
                    if content:
                        lines.append(f"- {content}")
        else:
            # Legacy format: fact_id -> content string
            lines.append("")  # Add blank line
            for content in preferences.values():
                if content:
                    lines.append(f"- {content}")
        
        return '\n'.join(lines) if len(lines) > 1 else ""

    def output_parser(result: AIMessage, name) -> Any:
        result = AIMessage(content=result.content, name=name)
        return result

    async def run(self, input_variables: AgentState) -> AIMessage:
        data = input_variables.model_dump()
        data['variables_summary'] = input_variables.variables_manager.get_variables_summary()
        base_instructions = instructions_manager.get_instructions(self.name) or ""
        data["instructions"] = base_instructions
        data["user_preferences_context"] = ""

        # Inject user preferences into the prompt if available
        if input_variables.user_preferences:
            preferences_text = self._format_preferences_for_prompt(input_variables.user_preferences)
            if preferences_text:
                # Append preferences to instructions
                data["instructions"] = data["instructions"] + "\n\n" + preferences_text
            data["user_preferences_context"] = self._format_preferences_for_decision_context(
                input_variables.user_preferences
            )

        res = await self.chain.ainvoke(data)

        if not self.thoughts_enabled:
            lite_res = res
            if settings.advanced_features.api_planner_hitl:
                full_res = APIPlannerOutput(
                    thoughts=[],
                    action=lite_res.action,
                    action_input_shortlisting_agent=lite_res.action_input_shortlisting_agent,
                    action_input_coder_agent=lite_res.action_input_coder_agent,
                    action_input_conclude_task=lite_res.action_input_conclude_task,
                    action_input_consult_with_human=lite_res.action_input_consult_with_human,
                )
            else:
                full_res = APIPlannerOutput(
                    thoughts=[],
                    action=lite_res.action,
                    action_input_shortlisting_agent=lite_res.action_input_shortlisting_agent,
                    action_input_coder_agent=lite_res.action_input_coder_agent,
                    action_input_conclude_task=lite_res.action_input_conclude_task,
                    action_input_consult_with_human=None,
                )
            return AIMessage(content=full_res.model_dump_json())
        else:
            if not settings.advanced_features.api_planner_hitl:
                if hasattr(res, 'action_input_consult_with_human'):
                    res_dict = res.model_dump()
                    res_dict['action_input_consult_with_human'] = None
                    full_res = APIPlannerOutput(**res_dict)
                    return AIMessage(content=full_res.model_dump_json())
            return AIMessage(content=res.model_dump_json())

    @staticmethod
    def create():
        dyna_model = settings.agent.planner.model

        if settings.advanced_features.api_planner_hitl:
            system_prompt = "./prompts/system_hitl.jinja2"
            user_prompt = "./prompts/user_hitl.jinja2"
        else:
            system_prompt = "./prompts/system.jinja2"
            user_prompt = "./prompts/user.jinja2"

        return APIPlannerAgent(
            prompt_template=load_prompt_simple(
                system_prompt,
                user_prompt,
                model_config=dyna_model,
            ),
            llm=llm_manager.get_model(dyna_model),
        )
