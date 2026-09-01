from enum import Enum


class ActionName(str, Enum):
    CODER_AGENT = "CoderAgent"
    API_FILTERING_AGENT = "ApiShortlistingAgent"
    CONCLUDE_TASK = "ConcludeTask"
    CONSULT_WITH_HUMAN = "ConsultWithHuman"
