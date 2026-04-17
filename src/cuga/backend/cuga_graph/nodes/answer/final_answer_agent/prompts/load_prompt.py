import re
from typing import List, Literal, Any, Optional

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field

from cuga.backend.llm.utils.helpers import load_prompt_simple

_ANSWER_LINE = re.compile(
    r"(?is)(?:^|\n)\s*(?:completion\s+)?answer:\s*(.*)$",
)


class FinalAnswerOutput(BaseModel):
    thoughts: List[str] = Field(..., description="Your thoughts that leads to final answer")

    final_answer: str = Field(..., description="Final answer")


class FinalAnswerAppworldOutput(BaseModel):
    """
    Represents the output structure for the AI assistant's response.
    """

    thoughts: List[str] = Field(
        ...,
        description="A list of strings, where each string is a distinct point in the reasoning process for arriving at the final_answer.",
    )
    final_answer: str = Field(
        ...,
        description="The determined output value based on the user intent and system answer. Can be an empty string, a specific extracted value, or the original system answer.",
    )
    final_answer_type: Literal['str', 'int', 'float'] = Field(
        ..., description="The Python data type of the final_answer. Must be 'str', 'int', or 'float'."
    )


parser = PydanticOutputParser(pydantic_object=FinalAnswerOutput)


def load_appworld_final_answer_prompt(model_config: Optional[Any] = None) -> ChatPromptTemplate:
    """Chat prompt for AppWorld benchmark final-answer formatting (system + user templates)."""
    return load_prompt_simple(
        "system_appworld.jinja2",
        "user_msg_appworld.jinja2",
        model_config=model_config,
        relative_to_caller=True,
    )


def load_appworld_plain_final_answer_prompt(model_config: Optional[Any] = None) -> ChatPromptTemplate:
    """AppWorld final-answer prompts: plain `answer:` line, no JSON (see system_appworld_plain.jinja2)."""
    return load_prompt_simple(
        "system_appworld_plain.jinja2",
        "user_msg_appworld_plain.jinja2",
        model_config=model_config,
        relative_to_caller=True,
    )


def parse_appworld_plain_completion(raw: str) -> str:
    """Parse `answer:` / `completion answer:` line; strip fences and whitespace."""
    text = (raw or "").strip()
    text = re.sub(r"^```\w*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    m = _ANSWER_LINE.search(text)
    if m:
        return m.group(1).strip()
    return text


def appworld_plain_llm_to_structured(msg: Any) -> FinalAnswerAppworldOutput:
    """Map raw LLM message content to FinalAnswerAppworldOutput (thoughts empty, type str)."""
    from langchain_core.messages import BaseMessage

    if isinstance(msg, BaseMessage):
        raw = msg.content
    else:
        raw = msg
    if not isinstance(raw, str):
        raw = str(raw)
    final = parse_appworld_plain_completion(raw)
    return FinalAnswerAppworldOutput(
        thoughts=[],
        final_answer=final,
        final_answer_type="str",
    )


def appworld_plain_post_llm_runnable() -> RunnableLambda:
    return RunnableLambda(appworld_plain_llm_to_structured)
