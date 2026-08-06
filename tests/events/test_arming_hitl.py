"""HITL arming — nothing is armed until the human approves the exact prompt.

Covers the whole dialogue over the real HTTP surface: propose → (clarify) → CONFIRM →
yes / edit / cancel, plus the two properties that make it trustworthy — the parked dialogue is
DURABLE (survives a process restart) and an unclear reply never counts as approval.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cuga.backend.events.agent_store import AgentStore
from cuga.backend.events.app import register_events_routes
from cuga.backend.events.arming import (
    ARMED,
    CANCELLED,
    CONFIRM,
    NEEDS_INPUT,
    compose_prompt,
    read_reply,
    validate,
)
from cuga.backend.events.concierge import Concierge
from cuga.backend.events.runtime import AgentSpec, AgentStoreRuntime, StubRuntime
from cuga.backend.events.subscriptions import SubscriptionStore


def _client(db=":memory:", stub=False):
    """stub=True swaps in the deterministic runtime — used by the tests that send PLAIN CHAT,
    which would otherwise build the real CUGA worker graph (slow, needs a live model)."""
    rt = StubRuntime() if stub else AgentStoreRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(AgentSpec(name="cuga", prompt="c", integrations=[]), scope="default")
    store = SubscriptionStore(db)
    cg = Concierge(rt, store=store, engine=None)
    app = FastAPI()
    register_events_routes(app, runtime=rt, store=store, concierge=cg, engine=None, gateway_token="")
    return TestClient(app), store


def _say(c, text, thread="web:local"):
    r = c.post("/api/concierge", json={"text": text, "thread_id": thread})
    assert r.status_code == 200, r.text
    return r.json()


# ── the pure state machine ─────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "text,action",
    [
        ("yes", "yes"),
        ("Yes.", "yes"),
        ("ok", "yes"),
        ("arm it", "yes"),
        ("lgtm", "yes"),
        ("cancel", "cancel"),
        ("no", "cancel"),
        ("/cancel", "cancel"),
        ("never mind", "cancel"),
        ("change the prompt to fetch IBM", "edit"),
        ("edit schedule: every 9 minutes", "edit"),
        ("every 10 minutes", "edit"),
        ("what's the weather?", "unclear"),
        ("", "unclear"),
    ],
)
def test_read_reply(text, action):
    assert read_reply(text)[0] == action


def test_read_reply_extracts_the_edited_field_and_value():
    action, field, value = read_reply("change the prompt to Fetch the IBM share price")
    assert (action, field) == ("edit", "prompt")
    assert value == "Fetch the IBM share price"


def test_compose_prompt_strips_cadence_and_delivery_scaffolding():
    """The fire-time prompt is the TASK; when and where-to-send are the subscription's job."""
    out = compose_prompt("every 5 minutes send me the IBM stock price", "cron")
    assert "every 5 minutes" not in out.lower()
    assert "ibm stock price" in out.lower()
    assert out.endswith(".")


def test_compose_prompt_never_returns_empty():
    assert compose_prompt("every 5 minutes", "cron").strip()


def test_validate_asks_for_a_missing_cadence_instead_of_defaulting():
    q, field = validate({"kind": "cron", "utterance": "tell me a fun fact"})
    assert field == "schedule" and "how often" in q.lower()
    assert validate({"kind": "cron", "utterance": "every 5 minutes tell me a fun fact"}) == ("", "")


# ── the dialogue, over HTTP ────────────────────────────────────────────────────────────────────
def test_confirm_then_yes_arms(monkeypatch):
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _client()
    out = _say(c, "/automate every 5 minutes send IBM stock price")
    assert out["state"] == CONFIRM
    assert store.list() == []
    # the card shows the exact prompt the agent will be handed
    assert "IBM" in out["summary"]["prompt"]
    assert "every 5 minute" in out["summary"]["trigger"]
    assert _say(c, "yes")["state"] == ARMED
    assert len(store.list()) == 1


def test_cancel_arms_nothing(monkeypatch):
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _client(stub=True)  # this one also sends plain chat at the end
    assert _say(c, "/automate every 5 minutes send IBM stock price")["state"] == CONFIRM
    assert _say(c, "cancel")["state"] == CANCELLED
    assert store.list() == []
    # and the thread is released — a later chat message is no longer part of an arming dialogue
    assert _say(c, "hello there").get("state") in (None, "")


def test_edit_prompt_then_arm_uses_the_edited_prompt(monkeypatch):
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _client()
    _say(c, "/automate every 5 minutes send IBM stock price")
    out = _say(c, "change the prompt to Report the IBM share price in USD")
    assert out["state"] == CONFIRM
    assert out["summary"]["prompt"] == "Report the IBM share price in USD"
    assert store.list() == [], "an edit must not arm"
    assert _say(c, "yes")["state"] == ARMED
    assert "USD" in (store.list()[0].prompt or "")


def test_missing_cadence_is_asked_not_defaulted(monkeypatch):
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _client()
    out = _say(c, "/schedule send me the IBM stock price")
    assert out["state"] == NEEDS_INPUT and "how often" in out["question"].lower()
    assert store.list() == []
    out2 = _say(c, "every 5 minutes")
    assert out2["state"] == CONFIRM and "every 5 minute" in out2["summary"]["trigger"]
    assert _say(c, "yes")["state"] == ARMED


def test_an_unclear_reply_is_not_approval(monkeypatch):
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _client()
    _say(c, "/automate every 5 minutes send IBM stock price")
    out = _say(c, "hmm what does that even mean")
    assert out["state"] == CONFIRM, "an ambiguous reply must re-ask, never arm"
    assert store.list() == []


def test_two_threads_arm_independently(monkeypatch):
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _client()
    _say(c, "/automate every 5 minutes send IBM stock price", thread="gw:slack:C1")
    _say(c, "/automate every 9 minutes send TSLA stock price", thread="gw:slack:C2")
    assert _say(c, "cancel", thread="gw:slack:C1")["state"] == CANCELLED
    assert _say(c, "yes", thread="gw:slack:C2")["state"] == ARMED
    assert len(store.list()) == 1


def test_parked_dialogue_survives_a_restart(tmp_path, monkeypatch):
    """The dialogue lives in the events DB, not process memory: a redeploy mid-arm used to drop
    it silently, so the user's next 'yes' fell through to chat."""
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    db = str(tmp_path / "events.db")
    c1, _ = _client(db)
    assert _say(c1, "/automate every 5 minutes send IBM stock price")["state"] == CONFIRM
    c2, store2 = _client(db)  # a brand-new process against the same store
    assert _say(c2, "yes")["state"] == ARMED
    assert len(store2.list()) == 1


def test_cancel_with_nothing_in_flight_is_a_no_op():
    c, store = _client()
    out = _say(c, "/cancel")
    assert out["state"] == CANCELLED and store.list() == []


def test_plain_chat_never_enters_the_arming_dialogue():
    c, store = _client(stub=True)
    assert _say(c, "what is the capital of Japan?").get("state") in (None, "")
    assert store.list() == []


# ── channels: the SAME dialogue over /invoke ───────────────────────────────────────────────────
def _channel_say(c, text, thread="gw:slack:C42#170.1"):
    """What a channel adapter posts after its @mention gate has stripped the mention."""
    r = c.post(
        "/invoke",
        json={
            "text": text,
            "agent": "concierge",
            "deliver": False,
            "source": {"type": "channel", "name": "slack", "thread_id": thread, "user": "U1"},
            "event": {"kind": "message", "payload": {}},
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_channel_arming_dialogue_matches_the_web_one(monkeypatch):
    """A Slack/Discord @mention arrives with the mention already stripped, so the SAME slash +
    CONFIRM dialogue applies — no separate channel code path."""
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _client()
    out = _channel_say(c, "/automate every 5 minutes send IBM stock price")
    assert out["state"] == CONFIRM and store.list() == []
    assert _channel_say(c, "yes")["state"] == ARMED
    assert len(store.list()) == 1


def test_channel_arm_records_the_originating_conversation(monkeypatch):
    """Fires must come back to the thread the human armed from — the sink is the origin thread."""
    monkeypatch.setenv("EVENTS_SCHEDULER", "native")
    c, store = _client()
    out = _channel_say(c, "/automate every 5 minutes send IBM stock price")
    assert "slack" in out["summary"]["delivery"]
    _channel_say(c, "yes")
    assert "gw:slack:C42" in (store.list()[0].thread_id or "")


def _door_pattern():
    """CUGA's _SLASH_VERBS, read from source rather than imported.

    Importing cuga.backend.server.main pulls the whole CUGA server in — module-level side effects
    that leak into every other test in this session (17 unrelated failures when it was imported
    here). The events suite must stay light; lift the literal out instead.
    """
    import pathlib
    import re

    src = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "cuga" / "backend" / "server" / "main.py"
    ).read_text()
    m = re.search(r"_SLASH_VERBS = re\.compile\(\s*(r\"[^\"]+\")", src)
    assert m, "could not find _SLASH_VERBS in server/main.py — did it move or get renamed?"
    return re.compile(eval(m.group(1)), re.I)


def test_the_door_and_the_concierge_agree_on_what_a_slash_command_is():
    """THE GATE LEAK. CUGA's door decides "is this arming?" and the concierge decides "which arming
    verb is it?". If the door is MORE permissive than the parser, the extra utterances are forwarded
    here, missed by _slash_parse, and handled by the NL path — which ARMS DIRECTLY, with no
    confirmation card. Observed on Code Engine: "a subscription was armed BEFORE the human
    confirmed — the gate leaked", after the door learned to tolerate a leading @mention and this
    parser had not.

    They must recognise exactly the same shapes. This test fails if either side drifts."""
    from cuga.backend.events.concierge import _slash_parse

    _SLASH_VERBS = _door_pattern()

    forwarded_by_the_door = [
        "/automate every 5 mins check ibm",
        "  /schedule daily briefing",
        "<@U0BFR0NS7ME> /automate every minute send me the price of bitcoin",
        "<@U1> <@U2> /poll watch the repo",
        "/watch box for new files",
        "/push github",
    ]
    for text in forwarded_by_the_door:
        assert _SLASH_VERBS.match(text), f"door should forward: {text!r}"
        assert _slash_parse(text) is not None, (
            f"door forwards {text!r} but _slash_parse misses it → the NL path arms it with no "
            f"confirmation. The gate leaks."
        )

    # and neither side may claim ordinary chat
    for text in ("what is /automate?", "hello", "tell me about /cron jobs"):
        assert not _SLASH_VERBS.match(text), text
        assert _slash_parse(text) is None, text


def test_cancel_is_the_doors_verb_only():
    """/cancel is handled by the arming GATE (it drops a parked draft), not by _slash_parse — so
    the door must forward it even though the parser returns None. Documented so the lockstep test
    above is not 'fixed' by adding /cancel to the parser."""
    from cuga.backend.events.concierge import _slash_parse

    _SLASH_VERBS = _door_pattern()
    assert _SLASH_VERBS.match("/cancel")
    assert _slash_parse("/cancel") is None
