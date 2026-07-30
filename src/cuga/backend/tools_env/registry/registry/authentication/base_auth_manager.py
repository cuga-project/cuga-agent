import time
from abc import ABC, abstractmethod
from typing import Dict, Optional

# AppWorld mints tokens with a random 10–30 min TTL (see apps/lib/apis/authentication.py:
# random.randrange(10*60, 30*60)). The 10-min floor is guaranteed, so a token re-fetched
# once it reaches this age is always still valid at refresh time — no need to decode the
# token's exp to know that. Trade-off: a token that happened to get a 30-min life is
# refreshed early (a few extra logins), which is cheap and harmless.
# ponytail: set very high (e.g. 10**9) to effectively disable auto-refresh.
REFRESH_AFTER_SECONDS = 9 * 60


class BaseAuthManager(ABC):
    def __init__(self):
        self._tokens: Dict[str, str] = {}
        # Wall-clock time each token was last fetched, for the age-based refresh below.
        self._token_times: Dict[str, float] = {}

    def _store(self, app_name: str, token: str) -> None:
        self._tokens[app_name] = token
        self._token_times[app_name] = time.time()

    def _is_stale(self, app_name: str) -> bool:
        """True if we have no fetch time or the token is at/over the refresh age."""
        ts = self._token_times.get(app_name)
        return ts is None or (time.time() - ts) >= REFRESH_AFTER_SECONDS

    def get_access_token(self, app_name: str) -> Optional[str]:
        """Get access token for app_name. Reuses the stored token while it is fresh;
        once it reaches REFRESH_AFTER_SECONDS it is re-fetched and stored."""
        stored_token = self._tokens.get(app_name)
        if stored_token and not self._is_stale(app_name):
            return stored_token

        # No token yet, or the stored one is stale — fetch a fresh one.
        creds = self._get_credentials(app_name)
        if creds is None:
            # Can't refresh (unknown app / no password): keep whatever we had.
            # stored_token is None when we never had one — same as the original contract.
            return stored_token

        try:
            token_info = self._fetch_token(app_name, creds)
            token = token_info.get("access_token")
            if not token:
                raise Exception("Failed to obtain access token")

            self._store(app_name, token)
            return token
        except Exception:
            # A stale token may still be valid (TTL is up to 30 min) — prefer it over
            # erroring so an in-flight call can still try. Only re-raise (preserving the
            # detailed message) when we have nothing to fall back on.
            if stored_token:
                return stored_token
            raise

    def refresh_stale(self) -> None:
        """Re-login any stored token that has reached the refresh age. Best-effort:
        a failed refresh keeps the existing (possibly still-valid) token."""
        for app_name in list(self._tokens.keys()):
            if self._is_stale(app_name):
                try:
                    self.get_access_token(app_name)
                except Exception:
                    pass

    def clear_tokens(self):
        """Clear all stored tokens. Used when reset endpoint is called."""
        self._tokens.clear()
        self._token_times.clear()

    def get_stored_token(self, app_name: str) -> Optional[str]:
        """Get token from memory by app_name."""
        return self._tokens.get(app_name)

    def get_stored_tokens(self) -> dict:
        """Get all stored tokens, refreshing any that have aged out first. This is the
        chokepoint that feeds the `_tokens` header (which carries the file_system token
        auto-injected into cross-app calls), so refreshing here keeps that token fresh
        without the agent — or the dropped file_system_access_token param — ever seeing it."""
        self.refresh_stale()
        return self._tokens

    @abstractmethod
    def _get_credentials(self, app_name: str) -> Optional[str]:
        """Return password (or other creds) for app_name, or None if unknown."""
        pass

    @abstractmethod
    def _fetch_token(self, app_name: str, creds: str) -> dict:
        """Hit your auth endpoint and return its JSON response."""
        pass
