import os
from enum import Enum
from cuga.config import settings


class ServiceType(str, Enum):
    OPENAPI = "openapi"
    TRM = "tool-runtime-manager"
    MCP_SERVER = "mcp_server"


LOCAL_ORCHESTRATE_URL = f"http://localhost:{settings.server_ports.orchestrate_url}"
LOCAL_TRM_URL = f"http://localhost:{settings.server_ports.trm_url}"

STATELESS_ACTIONS = [
    "go_back",
    "Restart",
    "Wait",
    'read_page',
    'to_google',
    'go_back',
]
ONLY_VALUE_ACTIONS = ['update_plan', 'answer', 'human_in_the_loop', 'send_msg_to_user', 'User_response']
NO_BID_ACTIONS = [
    'update_plan',
    'answer',
    'webmcp_call',
    'observe_page',
    'human_in_the_loop',
    'send_msg_to_user',
    'read_page',
    'to_google',
    'go_back',
    'Restart',
    'Wait',
]
HUMAN_IN_THE_LOOP_FUNC_NAME = ['human_in_the_loop', 'send_msg_to_user']
ANSWER_KEYS = ['text', 'content', 'answer', 'message']
ARGS_KEY = 'args'
STATE_KEY = 'state'
ID_KEY = 'id'
EMPTY_STATE_ID = 'empty_state'
UNKNOWN_ID = 'unknown'
FINAL_ANSWER = 'FINAL_ANSWER'
PROJECT_TEST_ROOT = os.path.join(os.getcwd(), 'evaluation')


BROWSERGYM_ID_ATTRIBUTE = "bid"  # Playwright's default is "data-testid"
BROWSERGYM_VISIBILITY_ATTRIBUTE = "browsergym_visibility_ratio"
BROWSERGYM_SETOFMARKS_ATTRIBUTE = "browsergym_set_of_marks"
EXTRACT_OBS_MAX_TRIES = 5
