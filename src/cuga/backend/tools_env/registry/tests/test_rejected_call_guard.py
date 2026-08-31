"""Unit tests for the rejected-call guard (#599).

The guard must stop an identical API call being re-issued after the registry
has definitively rejected it, while never blocking the legitimate recovery
path: fixing a precondition with a different (mutating) call and then retrying
the previously-rejected one unchanged.
"""

from types import SimpleNamespace

import pytest

from cuga.backend.tools_env.registry.registry.rejected_call_guard import (
    GUARDED_STATUS_CODES,
    RejectedCallGuard,
)


def _set_thresholds(monkeypatch, escalate_after=1, block_after=2):
    """Pin thresholds via cuga.config.settings (read lazily inside the guard),
    immune to dynaconf state left behind by other tests in the full suite."""
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(
                rejected_call_escalate_after=escalate_after,
                rejected_call_block_after=block_after,
            )
        ),
    )


ARGS = {"payment_card_id": 247, "address_id": 112}


def _reject(guard, args=ARGS, status_code=422, message="Invalid.", **kw):
    return guard.record_rejection("amazon", "post_orders", args, status_code, message, **kw)


@pytest.mark.unit
def test_signature_is_argument_order_insensitive():
    a = RejectedCallGuard.signature("app", "fn", {"a": 1, "b": 2})
    b = RejectedCallGuard.signature("app", "fn", {"b": 2, "a": 1})
    assert a == b


@pytest.mark.unit
def test_signature_ignores_access_token_rotation():
    """A refreshed token must not make a logically identical call look new."""
    a = RejectedCallGuard.signature("app", "fn", {"x": 1, "access_token": "old"})
    b = RejectedCallGuard.signature("app", "fn", {"x": 1, "access_token": "new"})
    assert a == b


@pytest.mark.unit
def test_different_args_are_independent_signatures(monkeypatch):
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    _reject(guard)
    _reject(guard)
    # Same endpoint with changed arguments must not be short-circuited.
    assert guard.check("amazon", "post_orders", {"payment_card_id": 999}) is None


@pytest.mark.unit
def test_escalate_on_second_rejection_block_on_third_attempt(monkeypatch):
    """The recommended tiering: 1st rejection passes through unchanged, the 2nd
    carries the escalated message, the 3rd+ attempt never reaches the API."""
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()

    # Attempt 1: allowed, rejection recorded, message unchanged.
    assert guard.check("amazon", "post_orders", ARGS) is None
    assert _reject(guard, message="Not enough balance.") is None

    # Attempt 2: still allowed, but the repeat is made explicit.
    assert guard.check("amazon", "post_orders", ARGS) is None
    escalated = _reject(guard, message="Not enough balance.")
    assert escalated is not None
    assert "rejected 2 times" in escalated
    assert "Not enough balance." in escalated
    assert "Do not re-issue it unchanged" in escalated

    # Attempt 3: short-circuited with the standard exception shape.
    short = guard.check("amazon", "post_orders", ARGS)
    assert short is not None
    assert short["status"] == "exception"
    assert short["status_code"] == 422
    assert short["error_type"] == "RepeatedRejectedCall"
    assert short["function_name"] == "post_orders"
    assert "Not executed" in short["message"]
    assert "rejected 2 times" in short["message"]
    assert "Not enough balance." in short["message"]
    # The precondition nudge for the false-impossibility class (e.g. 2c544f9_1).
    assert "fixable precondition" in short["message"]


@pytest.mark.unit
@pytest.mark.parametrize("status_code", [401, 403, 408, 429, 500, 502, None])
def test_non_guarded_statuses_never_count(monkeypatch, status_code):
    """Auth/transient/server errors can start succeeding without the arguments
    changing, so identical retries must stay allowed."""
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    for _ in range(5):
        assert _reject(guard, status_code=status_code) is None
    assert guard.check("amazon", "post_orders", ARGS) is None


@pytest.mark.unit
def test_guarded_statuses_cover_definitive_4xx():
    assert GUARDED_STATUS_CODES == {400, 402, 404, 405, 409, 410, 422}


@pytest.mark.unit
def test_successful_mutation_clears_counters(monkeypatch):
    """The 2c544f9_1 recovery path: transaction rejected for insufficient
    balance, a top-up succeeds, then the identical transaction must be allowed."""
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    _reject(guard)
    _reject(guard)
    assert guard.check("amazon", "post_orders", ARGS) is not None

    # Mutating success — in ANY app — clears the block (the precondition fix
    # often lives in a different app than the failing call).
    guard.record_success("venmo", "POST")
    assert guard.check("amazon", "post_orders", ARGS) is None


@pytest.mark.unit
@pytest.mark.parametrize("method", ["GET", "get", "HEAD"])
def test_successful_read_does_not_clear(monkeypatch, method):
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    _reject(guard)
    _reject(guard)
    guard.record_success("amazon", method)
    assert guard.check("amazon", "post_orders", ARGS) is not None


@pytest.mark.unit
def test_unknown_method_treated_as_mutating(monkeypatch):
    """Wrongly clearing only weakens the guard; wrongly keeping a block could
    forbid a call that has become valid — so missing method clears."""
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    _reject(guard)
    _reject(guard)
    guard.record_success("amazon", None)
    assert guard.check("amazon", "post_orders", ARGS) is None


@pytest.mark.unit
def test_reset_clears_everything(monkeypatch):
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    _reject(guard)
    _reject(guard)
    guard.reset()
    assert guard.check("amazon", "post_orders", ARGS) is None
    # And the count restarts from zero: next rejection is a "first" again.
    assert _reject(guard) is None


@pytest.mark.unit
def test_zero_disables_blocking(monkeypatch):
    _set_thresholds(monkeypatch, block_after=0)
    guard = RejectedCallGuard()
    for _ in range(10):
        _reject(guard)
    assert guard.check("amazon", "post_orders", ARGS) is None


@pytest.mark.unit
def test_zero_disables_escalation(monkeypatch):
    _set_thresholds(monkeypatch, escalate_after=0, block_after=0)
    guard = RejectedCallGuard()
    for _ in range(10):
        assert _reject(guard) is None


@pytest.mark.unit
def test_agent_ids_are_isolated(monkeypatch):
    """Database mode serves multiple agents from one process — one agent's
    rejections must not block another's."""
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    _reject(guard, agent_id="agent-a")
    _reject(guard, agent_id="agent-a")
    assert guard.check("amazon", "post_orders", ARGS, agent_id="agent-a") is not None
    assert guard.check("amazon", "post_orders", ARGS, agent_id="agent-b") is None
    assert guard.check("amazon", "post_orders", ARGS) is None


@pytest.mark.unit
def test_short_circuit_mirrors_serving_flavor(monkeypatch):
    """AppWorld adapter rejections reach the client as HTTP 200 with an
    exception-shaped body (TextContent path); registry-raised ones as HTTP 4xx.
    The short-circuit must report which flavor to serve."""
    _set_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    _reject(guard, served_as_http_error=False)
    _reject(guard, served_as_http_error=False)
    short = guard.check("amazon", "post_orders", ARGS)
    assert short is not None
    assert short["served_as_http_error"] is False

    # Default (registry-raised) flavor.
    _reject(guard, args={"other": 1})
    _reject(guard, args={"other": 1})
    short = guard.check("amazon", "post_orders", {"other": 1})
    assert short["served_as_http_error"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_guards_textcontent_rejections(monkeypatch):
    """Regression for the wiring gap found in the 20260901 smoke run: AppWorld
    rejections arrive as TextContent whose text is exception-shaped JSON, taking
    the route's success branch. The guard must count them there (not treat them
    as mutating successes), escalate on the 2nd, and short-circuit the 3rd with
    HTTP 200 — mirroring how the real rejections were served."""
    import json as _json

    from cuga.backend.tools_env.registry.registry import api_registry_server as srv
    from cuga.backend.tools_env.registry.registry.rejected_call_guard import RejectedCallGuard

    _set_thresholds(monkeypatch)
    monkeypatch.setattr(srv, "rejected_call_guard", RejectedCallGuard())
    monkeypatch.setattr(srv, "database_mode", False)

    rejection_text = _json.dumps(
        {
            "status": "exception",
            "error_type": "HTTPError",
            "message": "422 Client Error: Unprocessable Entity",
            "status_code": 422,
            "method": "POST",
        }
    )

    class FakeText:
        def __init__(self, text):
            self.text = text

    class FakeReg:
        async def show_apis_for_app(self, app_name):
            return {"post_orders": {"secure": False, "method": "POST", "path": "/orders"}}

        async def call_function(self, **kwargs):
            return [FakeText(rejection_text)]

    # `registry`/`mcp_manager` are module globals normally assigned in lifespan.
    monkeypatch.setattr(srv, "registry", FakeReg(), raising=False)
    monkeypatch.setattr(srv, "mcp_manager", SimpleNamespace(auth_config={}), raising=False)

    request = srv.FunctionCallRequest(app_name="amazon", function_name="post_orders", args=ARGS)

    # 1st rejection: served unchanged (plain dict, not a JSONResponse).
    first = await srv.call_mcp_function(request)
    assert first["status"] == "exception"
    assert "[Repeated failure]" not in first["message"]

    # 2nd: escalated in place.
    second = await srv.call_mcp_function(request)
    assert "[Repeated failure]" in second["message"]

    # 3rd: short-circuited without reaching the API, served as HTTP 200 to
    # mirror the TextContent flavor, with no flavor key leaking to the client.
    third = await srv.call_mcp_function(request)
    from fastapi.responses import JSONResponse

    assert isinstance(third, JSONResponse)
    assert third.status_code == 200
    body = _json.loads(third.body)
    assert "Not executed" in body["message"]
    assert "served_as_http_error" not in body

    # A genuine success (non-exception text) on a mutating call clears the
    # block. It must be a DIFFERENT signature — the blocked one is refused at
    # check() before it could execute (the real flow: a top-up call succeeds,
    # then the previously-blocked payment goes through).
    async def call_ok(**kwargs):
        return [FakeText('{"order_id": 1}')]

    monkeypatch.setattr(srv.registry, "call_function", call_ok)
    topup = srv.FunctionCallRequest(app_name="amazon", function_name="post_topup", args={"amount": 50})
    ok = await srv.call_mcp_function(topup)
    assert ok == {"order_id": 1}

    # The identical previously-blocked call is allowed through again and its
    # counter has restarted: served unchanged, no escalation, no refusal.
    async def call_rejected(**kwargs):
        return [FakeText(rejection_text)]

    monkeypatch.setattr(srv.registry, "call_function", call_rejected)
    after_reset = await srv.call_mcp_function(request)
    assert after_reset["status"] == "exception"
    assert "[Repeated failure]" not in after_reset["message"]
    assert "Not executed" not in after_reset["message"]


@pytest.mark.unit
def test_defaults_used_when_settings_missing(monkeypatch):
    """getattr defaults keep the guard live even if settings.toml lacks the keys."""
    monkeypatch.setattr("cuga.config.settings", SimpleNamespace(advanced_features=SimpleNamespace()))
    guard = RejectedCallGuard()
    assert _reject(guard) is None
    assert _reject(guard) is not None  # escalate_after defaults to 1
    assert guard.check("amazon", "post_orders", ARGS) is not None  # block_after defaults to 2
