from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, Field
from typing_extensions import Annotated

from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.api_models import ApiDescription


class ActionNameNoHITL(str, Enum):
    CODER_AGENT = "CoderAgent"
    API_FILTERING_AGENT = "ApiShortlistingAgent"
    CONCLUDE_TASK = "ConcludeTask"


class ConcludeTaskStatus(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class CoderAgentInput(BaseModel):
    task_description: str
    relevant_apis: List[ApiDescription]
    context_variables_from_history: List[str]


class ApiShortlistingAgentInput(BaseModel):
    app_name: str
    task_description: str


class ConcludeTaskInput(BaseModel):
    status: ConcludeTaskStatus
    final_response: str
    summary_of_execution: Optional[str] = None


class ConsultWithHumanInput(BaseModel):
    question: str
    context: Optional[str] = None
    suggested_options: Optional[List[str]] = None


class APIPlannerOutput(BaseModel):
    thoughts: List[str]
    action: "ActionName"
    action_input_shortlisting_agent: Optional[ApiShortlistingAgentInput] = None
    action_input_coder_agent: Optional[CoderAgentInput] = None
    action_input_conclude_task: Optional[ConcludeTaskInput] = None
    action_input_consult_with_human: Optional[ConsultWithHumanInput] = None


class APIPlannerOutputLite(BaseModel):
    action: "ActionName"
    action_input_shortlisting_agent: Optional[ApiShortlistingAgentInput] = None
    action_input_coder_agent: Optional[CoderAgentInput] = None
    action_input_conclude_task: Optional[ConcludeTaskInput] = None
    action_input_consult_with_human: Optional[ConsultWithHumanInput] = None


class APIPlannerOutputWX(BaseModel):
    thoughts: List[str]
    action: "ActionName"
    action_input_shortlisting_agent: ApiShortlistingAgentInput
    action_input_coder_agent: CoderAgentInput
    action_input_conclude_task: ConcludeTaskInput
    action_input_consult_with_human: ConsultWithHumanInput


class APIPlannerOutputLiteNoHITL(BaseModel):
    action: ActionNameNoHITL
    action_input_shortlisting_agent: Optional[ApiShortlistingAgentInput] = None
    action_input_coder_agent: Optional[CoderAgentInput] = None
    action_input_conclude_task: Optional[ConcludeTaskInput] = None


from cuga.backend.cuga_graph.nodes.cuga_agent_core.schemas.action_names import ActionName  # noqa: E402

APIPlannerOutput.model_rebuild()
APIPlannerOutputLite.model_rebuild()
APIPlannerOutputWX.model_rebuild()
