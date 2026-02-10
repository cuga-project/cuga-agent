import datetime
import json
from cuga.backend.memory.agentic_memory.utils.logging import Logging
from cuga.backend.memory.agentic_memory.utils.utils import get_chat_model, clean_llm_response
from cuga.backend.memory.agentic_memory.config import milvus_config
from cuga.backend.memory.agentic_memory.schema import Message, Fact
from cuga.backend.memory.agentic_memory.categorization import CategoryManager
from cuga.config import settings
from langchain_core.prompts import PromptTemplate
from pathlib import Path
from pydantic import BaseModel

messages_tracker = {}
logger = Logging.get_logger()


class ExtractedFact(BaseModel):
    """Single extracted fact with categorization."""

    category: str
    key: str
    value: str
    content: str


class ExtractedFacts(BaseModel):
    """Legacy format for backward compatibility."""

    facts: list[str]


class CategorizedExtractedFacts(BaseModel):
    """New format with categorization."""

    facts: list[ExtractedFact]


async def extract_facts_from_messages(
    messages: list[Message], use_categorization: bool | None = None
) -> list[str] | list[Fact]:
    """
    Extract facts from messages with optional categorization.

    Args:
        messages: List of messages to extract facts from
        use_categorization: Whether to use categorization. If None, uses settings.

    Returns:
        List of fact strings (legacy) or List of Fact objects (with categorization)
    """
    llm = get_chat_model(milvus_config.fact_extraction)

    # Determine if categorization should be used
    if use_categorization is None:
        use_categorization = hasattr(settings, 'memory') and hasattr(settings.memory, 'categorization_mode')

    filtered_messages = [m.content for m in messages if m.role == 'user']
    messages_str = ""
    for one_msg in filtered_messages:
        messages_str += one_msg
        messages_str += "\n"

    # Format datetime as string to avoid Jinja2 SecurityError when accessing .strftime()
    prompt_input = {
        "current_datetime": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user_messages": messages_str,
    }

    # Select appropriate prompt template based on categorization mode
    if use_categorization:
        category_manager = CategoryManager()
        categories_info = category_manager.get_available_categories()

        if categories_info["type"] == "predefined_only":
            # Convert dict to list of tuples to avoid Jinja2 SecurityError with .items()
            categories_dict = categories_info["descriptions"]
            prompt_input["categories"] = [(k, v) for k, v in categories_dict.items()]
            prompt_file = Path(__file__).parent / "prompts/fact_extraction_predefined.jinja2"
        else:
            # For future dynamic/hybrid modes
            prompt_file = Path(__file__).parent / "prompts/fact_extraction.jinja2"
    else:
        # Legacy mode without categorization
        prompt_file = Path(__file__).parent / "prompts/fact_extraction.jinja2"

    failure_analysis_prompt = PromptTemplate.from_file(
        prompt_file, template_format="jinja2", encoding='utf-8'
    )
    formatted_prompt = failure_analysis_prompt.format(**prompt_input)
    response = (await llm.ainvoke(formatted_prompt)).content
    response = clean_llm_response(response)

    caught = None
    for attempt in range(3):
        try:
            if use_categorization:
                # Parse categorized facts
                extracted_facts = CategorizedExtractedFacts.model_validate(json.loads(response))
                # Convert to Fact objects
                facts = []
                for fact in extracted_facts.facts:
                    facts.append(
                        Fact(
                            content=fact.content,
                            category=fact.category,
                            key=fact.key,
                            value=fact.value,
                            # Note: category/key/value will be stored in metadata by the storage backend
                        )
                    )
                return facts
            else:
                # Legacy format
                extracted_facts = ExtractedFacts.model_validate(json.loads(response))
                return extracted_facts.facts
        except Exception as e:
            caught = e
            logger.warning(f"Attempt {attempt + 1} failed to parse facts: {e}")
            continue
    raise caught
