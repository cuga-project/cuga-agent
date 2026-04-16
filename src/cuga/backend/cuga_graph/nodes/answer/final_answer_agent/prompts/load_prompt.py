from typing import List, Literal, Any, Optional

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from cuga.backend.llm.utils.helpers import load_prompt_simple


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
