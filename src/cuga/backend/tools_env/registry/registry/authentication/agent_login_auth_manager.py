from typing import Optional

from cuga.backend.tools_env.registry.registry.authentication.base_auth_manager import BaseAuthManager

# Auth type for apps where the agent must log in by itself (YAML: ``auth: {type: oauth2_agent}``).
AGENT_LOGIN_AUTH_TYPE = "oauth2_agent"


class AgentLoginAuthManager(BaseAuthManager):
    """Holds only the tokens the agent obtained by calling an app's login API.

    It never reads passwords and never logs in. The registry stores a token here
    when the agent's own ``/auth/token`` call succeeds, and attaches it to later
    calls of that app. With no stored token the call goes out without one, so
    the app answers with its own authentication error and the agent has to log
    in. A stored token is kept even once stale: re-login is the agent's job.
    """

    def _get_credentials(self, app_name: str) -> Optional[str]:
        return None

    def _fetch_token(self, app_name: str, creds: str) -> dict:
        raise RuntimeError(f"{app_name}: agent-login apps are never logged in by the registry")
