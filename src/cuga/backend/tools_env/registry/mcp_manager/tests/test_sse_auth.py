"""
Regression tests for issue #191:
SSE MCP servers with authentication — auth config must not be dropped.

The SSE branch of _create_transport must attach auth headers the same way
the HTTP/streamable-http branch does.
"""

from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from cuga.backend.tools_env.registry.config.config_loader import Auth, ServiceConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager():
    """Return an MCPManager instance without triggering any I/O."""
    from cuga.backend.tools_env.registry.mcp_manager.mcp_manager import MCPManager

    return MCPManager.__new__(MCPManager)


def _sse_config(auth: Auth | None = None, url: str = "https://example.com/sse") -> ServiceConfig:
    return ServiceConfig(url=url, transport="sse", auth=auth)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSSETransportAuth:
    """_create_transport('sse') must forward auth as headers."""

    def test_no_auth_passes_none_headers(self):
        """Without auth config, SSETransport should receive headers=None."""
        manager = _make_manager()
        config = _sse_config(auth=None)

        fake_transport = MagicMock()
        with patch(
            "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
            return_value=fake_transport,
        ) as MockSSE:
            result = manager._create_transport("test_server", config)

        MockSSE.assert_called_once_with(url=config.url, headers=None)
        assert result is fake_transport

    def test_bearer_auth_sets_authorization_header(self):
        """Bearer auth must produce Authorization: Bearer <token>."""
        manager = _make_manager()
        config = _sse_config(auth=Auth(type="bearer", value="my-secret-token"))

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
                return_value=fake_transport,
            ) as MockSSE,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            result = manager._create_transport("test_server", config)

        call_kwargs = MockSSE.call_args.kwargs
        assert call_kwargs["headers"] == {"Authorization": "Bearer my-secret-token"}
        assert result is fake_transport

    def test_header_auth_sets_custom_header(self):
        """Header auth must produce the named custom header."""
        manager = _make_manager()
        config = _sse_config(auth=Auth(type="header", value="abc123", key="X-API-Key"))

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
                return_value=fake_transport,
            ) as MockSSE,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            result = manager._create_transport("test_server", config)

        call_kwargs = MockSSE.call_args.kwargs
        assert call_kwargs["headers"] == {"X-API-Key": "abc123"}
        assert result is fake_transport

    def test_basic_auth_sets_authorization_header(self):
        """Basic auth must produce Authorization: Basic <base64(user:pass)>."""
        import base64

        manager = _make_manager()
        config = _sse_config(auth=Auth(type="basic", value="user:pass"))

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
                return_value=fake_transport,
            ) as MockSSE,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            result = manager._create_transport("test_server", config)

        expected = base64.b64encode(b"user:pass").decode()
        call_kwargs = MockSSE.call_args.kwargs
        assert call_kwargs["headers"] == {"Authorization": f"Basic {expected}"}
        assert result is fake_transport

    def test_api_key_auth_appends_query_params_to_url(self):
        """api-key auth must be URL-encoded and appended to the transport URL."""
        manager = _make_manager()
        config = _sse_config(auth=Auth(type="api-key", value="key123", key="api_key"))

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
                return_value=fake_transport,
            ) as MockSSE,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            result = manager._create_transport("test_server", config)

        call_kwargs = MockSSE.call_args.kwargs
        assert call_kwargs["headers"] is None
        assert call_kwargs["url"] == "https://example.com/sse?api_key=key123"
        assert result is fake_transport

    def test_query_auth_appends_custom_param_to_url(self):
        """query auth must append the named query parameter to the transport URL."""
        manager = _make_manager()
        config = _sse_config(auth=Auth(type="query", value="token456", key="auth_token"))

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
                return_value=fake_transport,
            ) as MockSSE,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            result = manager._create_transport("test_server", config)

        call_kwargs = MockSSE.call_args.kwargs
        assert call_kwargs["headers"] is None
        assert call_kwargs["url"] == "https://example.com/sse?auth_token=token456"
        assert result is fake_transport

    def test_api_key_auth_merges_with_existing_query_params(self):
        manager = _make_manager()
        config = _sse_config(
            auth=Auth(type="api-key", value="key123", key="api_key"),
            url="https://example.com/sse?existing=1",
        )

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
                return_value=fake_transport,
            ) as MockSSE,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            manager._create_transport("test_server", config)

        parsed = urlparse(MockSSE.call_args.kwargs["url"])
        assert parse_qs(parsed.query) == {"existing": ["1"], "api_key": ["key123"]}

    def test_query_auth_merges_with_existing_query_params(self):
        manager = _make_manager()
        config = _sse_config(
            auth=Auth(type="query", value="token456", key="auth_token"),
            url="https://example.com/sse?existing=1",
        )

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
                return_value=fake_transport,
            ) as MockSSE,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            manager._create_transport("test_server", config)

        parsed = urlparse(MockSSE.call_args.kwargs["url"])
        assert parse_qs(parsed.query) == {"existing": ["1"], "auth_token": ["token456"]}

    def test_api_key_auth_encodes_plus_in_value_with_quote(self):
        """Base64-like secrets with + must use %2B, not rely on quote_plus semantics."""
        manager = _make_manager()
        config = _sse_config(
            auth=Auth(type="api-key", value="abc+def/ghi=", key="api_key"),
        )

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport",
                return_value=fake_transport,
            ) as MockSSE,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            manager._create_transport("test_server", config)

        assert MockSSE.call_args.kwargs["url"] == "https://example.com/sse?api_key=abc%2Bdef%2Fghi%3D"

    def test_missing_url_raises(self):
        """SSE transport without a URL must raise immediately."""
        manager = _make_manager()
        config = ServiceConfig(transport="sse", auth=None)  # no url

        with patch("cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport", MagicMock()):
            with pytest.raises(Exception, match="SSE transport requires 'url'"):
                manager._create_transport("test_server", config)

    def test_sse_transport_unavailable_raises(self):
        """If SSETransport import failed, _create_transport must raise."""
        manager = _make_manager()
        config = _sse_config()

        with patch("cuga.backend.tools_env.registry.mcp_manager.mcp_manager.SSETransport", None):
            with pytest.raises(Exception, match="SSETransport not available"):
                manager._create_transport("test_server", config)


class TestHTTPTransportAuth:
    """_create_transport('http') must forward auth headers and query params."""

    def _http_config(self, auth: Auth | None = None, url: str = "https://example.com/mcp") -> ServiceConfig:
        return ServiceConfig(url=url, transport="http", auth=auth)

    def test_bearer_auth_sets_authorization_header(self):
        manager = _make_manager()
        config = self._http_config(auth=Auth(type="bearer", value="my-secret-token"))

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.StreamableHttpTransport",
                return_value=fake_transport,
            ) as MockHTTP,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            result = manager._create_transport("test_server", config)

        call_kwargs = MockHTTP.call_args.kwargs
        assert call_kwargs["headers"] == {"Authorization": "Bearer my-secret-token"}
        assert call_kwargs["url"] == config.url
        assert result is fake_transport

    def test_api_key_auth_appends_query_params_to_url(self):
        manager = _make_manager()
        config = self._http_config(auth=Auth(type="api-key", value="key123", key="api_key"))

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.StreamableHttpTransport",
                return_value=fake_transport,
            ) as MockHTTP,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            result = manager._create_transport("test_server", config)

        call_kwargs = MockHTTP.call_args.kwargs
        assert call_kwargs["headers"] is None
        assert call_kwargs["url"] == "https://example.com/mcp?api_key=key123"
        assert result is fake_transport

    def test_api_key_auth_merges_with_existing_query_params(self):
        manager = _make_manager()
        config = self._http_config(
            auth=Auth(type="api-key", value="key123", key="api_key"),
            url="https://example.com/mcp?existing=1",
        )

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.StreamableHttpTransport",
                return_value=fake_transport,
            ) as MockHTTP,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            manager._create_transport("test_server", config)

        parsed = urlparse(MockHTTP.call_args.kwargs["url"])
        assert parse_qs(parsed.query) == {"existing": ["1"], "api_key": ["key123"]}

    def test_query_auth_merges_with_existing_query_params(self):
        manager = _make_manager()
        config = self._http_config(
            auth=Auth(type="query", value="token456", key="auth_token"),
            url="https://example.com/mcp?existing=1",
        )

        fake_transport = MagicMock()
        with (
            patch(
                "cuga.backend.tools_env.registry.mcp_manager.mcp_manager.StreamableHttpTransport",
                return_value=fake_transport,
            ) as MockHTTP,
            patch(
                "cuga.backend.secrets.resolve_secret",
                side_effect=lambda v: v,
            ),
        ):
            manager._create_transport("test_server", config)

        parsed = urlparse(MockHTTP.call_args.kwargs["url"])
        assert parse_qs(parsed.query) == {"existing": ["1"], "auth_token": ["token456"]}
