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
def test_negative_thresholds_treated_as_disabled(monkeypatch):
    """A negative threshold must degrade to disabled — never block or escalate
    the very first rejection (count < negative is False for any count >= 1)."""
    _set_thresholds(monkeypatch, escalate_after=-1, block_after=-1)
    guard = RejectedCallGuard()
    for _ in range(5):
        assert _reject(guard) is None
    assert guard.check("amazon", "post_orders", ARGS) is None


@pytest.mark.unit
def test_non_integer_thresholds_fall_back_to_defaults(monkeypatch):
    _set_thresholds(monkeypatch, escalate_after="nonsense", block_after=None)
    guard = RejectedCallGuard()
    assert _reject(guard) is None
    assert _reject(guard) is not None  # default escalate_after=1
    assert guard.check("amazon", "post_orders", ARGS) is not None  # default block_after=2


@pytest.mark.unit
def test_escalate_at_or_above_block_still_blocks_and_warns_once(monkeypatch):
    """escalate_after >= block_after skips escalation by construction; blocking
    must still work, and the misconfiguration is flagged once per process."""
    _set_thresholds(monkeypatch, escalate_after=2, block_after=2)
    monkeypatch.setattr(RejectedCallGuard, "_warned_threshold_order", False)
    guard = RejectedCallGuard()
    assert _reject(guard) is None
    assert _reject(guard) is None  # count=2 <= escalate_after=2: no escalation
    short = guard.check("amazon", "post_orders", ARGS)
    assert short is not None and "Not executed" in short["message"]
    assert RejectedCallGuard._warned_threshold_order is True


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


# ── Same error, different arguments ────────────────────────────────────────


def _set_all_thresholds(monkeypatch, escalate_after=1, block_after=2, distinct_after=3):
    """Configure the independent exact-call and endpoint-advisory thresholds."""
    monkeypatch.setattr(
        "cuga.config.settings",
        SimpleNamespace(
            advanced_features=SimpleNamespace(
                rejected_call_escalate_after=escalate_after,
                rejected_call_block_after=block_after,
                rejected_call_distinct_args_advise_after=distinct_after,
            )
        ),
    )


def _reject_card(guard, card, message="The payment card has expired."):
    return guard.record_rejection("shop", "place_order", {"card": card}, 422, message)


@pytest.mark.unit
def test_advises_after_n_distinct_failures_without_blocking_new_arguments(monkeypatch):
    """Repeated error shapes justify advice, not refusing an untried card."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    for card in (1, 2):
        assert _reject_card(guard, card) is None
    advisory = _reject_card(guard, 3)
    assert "3 different argument sets" in advisory
    assert "The payment card has expired." in advisory
    assert "may still succeed" in advisory
    for card in (4, 5, 6):
        assert guard.check("shop", "place_order", {"card": card}) is None
        assert "[Repeated endpoint failure]" in _reject_card(guard, card)


@pytest.mark.unit
@pytest.mark.parametrize("quote", ['"', "'", "`"])
def test_ids_and_quoted_values_do_not_split_the_error_shape(monkeypatch, quote):
    """Quoted nonnumeric identifiers and numbers belong to the same shape."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    messages = [
        f"Card {quote}{name}{quote} has expired in {year}."
        for name, year in (("alpha", 2020), ("beta", 2021), ("gamma", 2022))
    ]
    assert _reject_card(guard, 1, messages[0]) is None
    assert _reject_card(guard, 2, messages[1]) is None
    assert "3 different argument sets" in _reject_card(guard, 3, messages[2])


@pytest.mark.unit
def test_contractions_preserve_meaningful_error_text():
    """Apostrophes inside words must not swallow different failure reasons."""
    active = RejectedCallGuard.error_shape("Card isn't active and doesn't exist")
    funded = RejectedCallGuard.error_shape("Card isn't funded and doesn't exist")
    assert active == "card isn't active and doesn't exist"
    assert funded == "card isn't funded and doesn't exist"
    assert active != funded


@pytest.mark.unit
def test_different_errors_do_not_accumulate(monkeypatch):
    """Only matching errors contribute to an advisory."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    for card, message in enumerate(
        ("The payment card has expired.", "The cart is empty.", "The promo code is not valid.")
    ):
        assert _reject_card(guard, card, message) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "other", [("other", "place_order", "a"), ("shop", "other", "a"), ("shop", "place_order", "b")]
)
def test_advisory_history_and_clearing_are_isolated(monkeypatch, other):
    """Other apps, functions and agents cannot contribute or clear history."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    for card in (1, 2):
        guard.record_rejection("shop", "place_order", {"card": card}, 422, "Expired", agent_id="a")
    app, fn, agent = other
    assert guard.record_rejection(app, fn, {"card": 3}, 422, "Expired", agent_id=agent) is None
    guard.record_success(app, "POST", function_name=fn, agent_id=agent)
    assert "3 different argument sets" in guard.record_rejection(
        "shop", "place_order", {"card": 3}, 422, "Expired", agent_id="a"
    )
    guard.record_success("shop", "POST", function_name="place_order", agent_id="a")
    assert guard.record_rejection("shop", "place_order", {"card": 4}, 422, "Expired", agent_id="a") is None


@pytest.mark.unit
@pytest.mark.parametrize("method", ["POST", "GET", "get", "HEAD", None])
def test_success_on_the_same_endpoint_clears_its_advisory(monkeypatch, method):
    """A successful read also invalidates previous endpoint failure evidence."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    for card in (1, 2, 3):
        _reject_card(guard, card)
    assert guard.check("shop", "place_order", {"card": 4}) is None
    guard.record_success("shop", method, function_name="place_order")
    assert _reject_card(guard, 5) is None


@pytest.mark.unit
@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_successful_read_clears_advice_but_preserves_exact_call_block(monkeypatch, method):
    """Read success resets endpoint advice without implying a state mutation."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    for card in (1, 1, 2, 3):
        _reject_card(guard, card)
    assert guard.check("shop", "place_order", {"card": 1}) is not None
    guard.record_success("shop", method, function_name="place_order")
    assert guard.check("shop", "place_order", {"card": 1}) is not None
    assert _reject_card(guard, 4) is None


@pytest.mark.unit
def test_success_elsewhere_does_not_reset_advisory_history(monkeypatch):
    """Interleaved add-to-cart successes must not suppress order-failure advice."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    messages = []
    for card in (1, 2, 3):
        messages.append(_reject_card(guard, card))
        guard.record_success("shop", "POST", function_name="add_to_cart")
    assert messages[:2] == [None, None]
    assert "3 different argument sets" in messages[2]
    assert guard.check("shop", "place_order", {"card": 4}) is None


@pytest.mark.unit
def test_success_still_clears_the_exact_signature_tiers(monkeypatch):
    """State changes allow a previously blocked exact call to run again."""
    _set_all_thresholds(monkeypatch, block_after=1)
    guard = RejectedCallGuard()
    _reject_card(guard, 1)
    assert guard.check("shop", "place_order", {"card": 1}) is not None
    guard.record_success("shop", "POST", function_name="add_to_cart")
    assert guard.check("shop", "place_order", {"card": 1}) is None


@pytest.mark.unit
def test_success_without_a_function_name_leaves_advisory_history_intact(monkeypatch):
    """Unidentified success cannot reset a specific endpoint's advice history."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    for card in (1, 2):
        _reject_card(guard, card)
    guard.record_success("shop", "POST")
    assert "3 different argument sets" in _reject_card(guard, 3)


@pytest.mark.unit
def test_reset_clears_the_endpoint_advisory(monkeypatch):
    """A new task must not inherit advice history."""
    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    for card in (1, 2, 3):
        _reject_card(guard, card)
    guard.reset()
    assert _reject_card(guard, 4) is None


@pytest.mark.unit
@pytest.mark.parametrize("threshold", [0, -1])
def test_disabled_advisory_does_not_accumulate_history(monkeypatch, threshold):
    """Disabled advice neither warns nor contributes stale evidence if enabled."""
    _set_all_thresholds(monkeypatch, distinct_after=threshold)
    guard = RejectedCallGuard()
    for card in (1, 2, 3, 4, 5):
        assert _reject_card(guard, card) is None
        assert guard.check("shop", "place_order", {"card": card}) is None
    _set_all_thresholds(monkeypatch)
    assert _reject_card(guard, 6) is None


@pytest.mark.unit
@pytest.mark.parametrize("threshold", [None, "invalid"])
def test_invalid_advisory_threshold_uses_default(monkeypatch, threshold):
    """Invalid configuration falls back to advice after three distinct failures."""
    _set_all_thresholds(monkeypatch, distinct_after=threshold)
    guard = RejectedCallGuard()
    assert _reject_card(guard, 1) is None
    assert _reject_card(guard, 2) is None
    assert "3 different argument sets" in _reject_card(guard, 3)
    assert guard.check("shop", "place_order", {"card": 4}) is None


@pytest.mark.unit
def test_advice_works_independently_of_exact_call_tiers(monkeypatch):
    """Disabling exact-call escalation and blocking does not disable advice."""
    _set_all_thresholds(monkeypatch, escalate_after=0, block_after=0)
    guard = RejectedCallGuard()
    assert _reject_card(guard, 1) is None
    assert _reject_card(guard, 2) is None
    assert "3 different argument sets" in _reject_card(guard, 3)
    assert guard.check("shop", "place_order", {"card": 3}) is None
    assert "3 different argument sets" in _reject_card(guard, 3)


@pytest.fixture
def advisory_route(monkeypatch):
    """Exercise the real HTTP route with only MCP execution and tracking stubbed."""
    from unittest.mock import AsyncMock

    from fastapi.testclient import TestClient
    from cuga.backend.tools_env.registry.registry import api_registry_server as srv

    _set_all_thresholds(monkeypatch)
    guard = RejectedCallGuard()
    registry = SimpleNamespace(show_apis_for_app=AsyncMock(), call_function=AsyncMock())
    monkeypatch.setattr(srv, "rejected_call_guard", guard)
    monkeypatch.setattr(srv, "registry", registry, raising=False)
    monkeypatch.setattr(srv, "mcp_manager", SimpleNamespace(auth_config={}), raising=False)
    monkeypatch.setattr(srv, "database_mode", False)
    monkeypatch.setattr(srv, "tracker", SimpleNamespace(collect_step_external=lambda *a, **kw: None))
    # No context manager: do not start the lifespan's external MCP services.
    client = TestClient(srv.app)
    try:
        yield client, registry
    finally:
        client.close()


@pytest.mark.unit
@pytest.mark.parametrize("http_error", [False, True], ids=["textcontent", "http-error"])
@pytest.mark.parametrize(
    "app,fn,method,bad_args,good_args,message,repair",
    [
        (
            "amazon",
            "place_order",
            "POST",
            [{"card": n} for n in (1, 2, 3)],
            {"card": 4},
            "The payment card has expired",
            None,
        ),
        (
            "venmo",
            "reset_password",
            "POST",
            [{"code": n} for n in (111, 222, 333)],
            {"code": 444},
            "Invalid password reset code",
            "request_password_reset",
        ),
        (
            "phone",
            "alarms",
            "POST",
            [{"repeat_days": v} for v in ("Mon", "Monday", "1")],
            {"repeat_days": [1]},
            "Validation error: repeat_days",
            None,
        ),
        (
            "file_system",
            "directory",
            "GET",
            [{"directory_path": "./", "substring": v} for v in ("a", "b", "c")],
            {"directory_path": "/"},
            "Directory not available",
            None,
        ),
        (
            "gmail",
            "mark_read",
            "POST",
            [{"thread_id": n} for n in (1, 2, 3, 4, 5)],
            {"thread_id": 6},
            "already marked as read",
            None,
        ),
    ],
    ids=[
        "valid-fourth-card",
        "fresh-reset-code",
        "corrected-validation",
        "corrected-directory",
        "idempotency-sweep",
    ],
)
def test_route_allows_recovery_and_preserves_error_delivery(
    advisory_route, http_error, app, fn, method, bad_args, good_args, message, repair
):
    """The actual route must execute the winning call after three or more failures."""
    import json
    from mcp.types import TextContent

    client, registry = advisory_route
    registry.show_apis_for_app.return_value = {
        fn: {"secure": False, "method": method},
        repair: {"secure": False, "method": "POST"},
    }
    error = {"status": "exception", "status_code": 422, "message": message, "error_type": "HTTPError"}

    def rejection():
        return dict(error) if http_error else [TextContent(type="text", text=json.dumps(error))]

    success = [TextContent(type="text", text='{"ok": true}')]
    registry.call_function.side_effect = (
        [rejection() for _ in bad_args] + ([success] if repair else []) + [success, rejection()]
    )

    def call(function, args):
        return client.post("/functions/call", json={"app_name": app, "function_name": function, "args": args})

    for index, args in enumerate(bad_args, start=1):
        response = call(fn, args)
        assert response.status_code == (422 if http_error else 200)
        body = response.json()
        assert body["status_code"] == 422
        assert body["error_type"] == "HTTPError"
        assert message in body["message"]
        assert "served_as_http_error" not in body
        assert ("[Repeated endpoint failure]" in body["message"]) == (index >= 3)
        assert registry.call_function.await_count == index
    if repair:
        assert call(repair, {}).json() == {"ok": True}
    recovered = call(fn, good_args)
    assert recovered.status_code == 200
    assert recovered.json() == {"ok": True}
    assert registry.call_function.call_args.kwargs["arguments"] == good_args
    # Success clears history, including for GET. A new failure is served plainly.
    after_success = call(fn, {**good_args, "probe": "new"})
    assert after_success.status_code == (422 if http_error else 200)
    assert after_success.json()["message"] == message
    assert registry.call_function.await_count == len(bad_args) + bool(repair) + 2
