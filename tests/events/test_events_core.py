"""Offline tests for the events layer's dependency-light core.

Runs with **plain python3** (stdlib only) OR pytest. It loads the pure modules directly
from ``src/cuga/backend/events`` (bypassing the heavy ``cuga`` package __init__), so it
needs no synced venv, no LLM, no AP. Covers: envelope · mcp_catalog · trace · flows ·
subscriptions · classify · StubRuntime.

    python3 tests/events/test_events_core.py        # direct
    uv run pytest tests/events/test_events_core.py  # via pytest
"""

import asyncio
import os
import sys

_EVENTS = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "src", "cuga", "backend", "events"))
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import classify          # noqa: E402
import concierge_plan    # noqa: E402
import envelope          # noqa: E402
import flows             # noqa: E402
import mcp_catalog       # noqa: E402
import subscriptions     # noqa: E402
import trace             # noqa: E402
import runtime           # noqa: E402


# ---- envelope ------------------------------------------------------------
def test_envelope_roundtrip():
    d = {"source": {"type": "integration", "name": "box", "thread_id": "sub:1"},
         "event": {"kind": "new_file", "payload": {"name": "resume.pdf"}},
         "text": "", "agent": "resume_judge", "deliver": True, "trace_id": "abc"}
    e = envelope.Envelope.from_dict(d)
    assert e.source.name == "box" and e.agent == "resume_judge" and e.deliver is True
    assert e.thread_id == "sub:1"
    assert envelope.Envelope.from_dict(e.to_dict()).event.payload["name"] == "resume.pdf"


def test_envelope_worker_input():
    ch = envelope.Envelope.from_dict({"source": {"type": "channel"}, "text": "capital of Japan?"})
    assert ch.worker_input() == "capital of Japan?"
    ig = envelope.Envelope.from_dict(
        {"source": {"type": "integration", "name": "box"},
         "event": {"kind": "new_file", "payload": {"name": "cv.pdf"}}})
    assert "cv.pdf" in ig.worker_input()


def test_envelope_push_fire_is_framed_one_shot():
    """An integration fire carries one-shot framing: trust the [event] payload (live probe: the
    agent summarized the WRONG PR by re-fetching), don't wait for future events, and reply with
    the deliverable content itself (live probe: "I've sent you the message" would have been what
    the sink delivered). Channel messages stay bare — no framing on chat."""
    fire = envelope.Envelope.from_dict(
        {"source": {"type": "integration", "name": "github"},
         "text": "whenever a PR is opened, summarize it and message me",
         "event": {"kind": "new_pr", "payload": {"number": "7", "title": "fix flaky test"}}})
    w = fire.worker_input()
    assert "watched event just occurred" in w and "[event]" in w
    assert "title=fix flaky test" in w
    assert w.index("watched event") < w.index("whenever a PR")   # framing comes FIRST
    ch = envelope.Envelope.from_dict({"source": {"type": "channel"}, "text": "hi"})
    assert "watched event" not in ch.worker_input()
    # a payload-less integration fire stays unframed (nothing authoritative to point at)
    bare = envelope.Envelope.from_dict(
        {"source": {"type": "integration", "name": "github"}, "text": "do the thing",
         "event": {"kind": "new_pr", "payload": {}}})
    assert "watched event" not in bare.worker_input()
    # webhook fires stay unframed too — the hook endpoint composes its own instruction text
    hook = envelope.Envelope.from_dict(
        {"source": {"type": "integration", "name": "webhook"}, "text": "Triage this payload: …",
         "event": {"kind": "message", "payload": {"alert": "HighCPU"}}})
    assert "watched event" not in hook.worker_input()
    # each payload field is capped — a full forwarded-email body blew up the delegate exchange
    # and the supervisor's raw deliberation got delivered as the answer (Slack, 2026-07-16)
    big = envelope.Envelope.from_dict(
        {"source": {"type": "integration", "name": "gmail"}, "text": "summarize it",
         "event": {"kind": "new_email", "payload": {"subject": "hi", "body": "x" * 50_000}}})
    w = big.worker_input()
    assert len(w) < 5_000 and "subject=hi" in w


def test_envelope_validate():
    assert envelope.validate({"source": {"type": "bogus"}})  # non-empty problems
    assert envelope.validate({"source": {"type": "channel"}, "text": ""})  # empty channel text
    assert not envelope.validate(
        {"source": {"type": "channel"}, "text": "hi", "event": {"kind": "message"}})


# ---- mcp_catalog ---------------------------------------------------------
def test_mcp_catalog():
    assert mcp_catalog.known_mcp_url("cuga-finance").endswith("mcp-finance"
                                                              ".1gxwxi8kos9y.us-east"
                                                              ".codeengine.appdomain.cloud/mcp")
    assert mcp_catalog.known_mcp_url("not-a-cuga") is None
    cfg = mcp_catalog.resolve(["cuga-geo", "cuga-nope"])
    assert list(cfg) == ["cuga-geo"] and cfg["cuga-geo"]["transport"] == "streamable_http"
    assert len(mcp_catalog.known_names()) == 7


# ---- trace ---------------------------------------------------------------
def test_trace():
    t = trace.Trace()
    assert len(t.id) == 12
    line = t("concierge", decision="CREATE", agent="resume_judge", mode="PUSH")
    assert f"[trace={t.id}]" in line and "decision=CREATE" in line and "mode=PUSH" in line
    assert trace.new_trace_id() != trace.new_trace_id()


# ---- flows ---------------------------------------------------------------
def test_flow_cron():
    f = flows.build_cron_flow(agent="papers", thread_id="gw:telegram:42", prompt="top arxiv",
                              interval_seconds=120,
                              sink=flows.send_step("telegram", "42", "{{step_1.body.answer}}"))
    trig = f["trigger"]
    assert trig["settings"]["pieceName"] == flows.PIECE["schedule"]
    assert trig["settings"]["input"]["minutes"] == 2
    step1 = trig["nextAction"]
    assert step1["settings"]["input"]["url"].endswith("/invoke")
    assert step1["settings"]["input"]["body"]["agent"] == "papers"
    assert step1["settings"]["input"]["body"]["source"]["type"] == "time"
    assert step1["nextAction"]["settings"]["pieceName"] == flows.PIECE["telegram"]


def test_flow_push_router():
    branches = [
        {"name": "MATCH", "match": "MATCH",
         "action": flows.send_step("gmail", "me@x.com", "{{step_1.body.answer}}")},
        {"name": "SKIP", "match": None, "action": None},
    ]
    f = flows.build_push_flow(agent="resume_judge", thread_id="sub:1", prompt="judge",
                              source="box", source_input={"folderId": "1"}, branches=branches)
    trig = f["trigger"]
    assert trig["settings"]["pieceName"] == flows.PIECE["box"]
    assert trig["settings"]["triggerName"] == "new_file"
    router = trig["nextAction"]["nextAction"]
    assert router["type"] == "ROUTER"
    assert [b["branchName"] for b in router["settings"]["branches"]] == ["MATCH", "SKIP"]
    assert router["children"][0]["settings"]["pieceName"] == flows.PIECE["gmail"]
    assert router["children"][1] is None


def test_flow_dispatcher_and_inbound():
    assert flows.build_flow("POLL", agent="p", thread_id="t", prompt="x",
                            interval_seconds=60).get("__mode") == "poll"
    inb = flows.build_inbound_flow(channel="telegram", agent="concierge")
    # the CHANNELS descriptor's trigger — the SAME one the live path (ap_engine.create_inbound_flow)
    # arms. This used to assert "new_message", a name that had silently drifted from the live flow.
    assert inb["trigger"]["settings"]["triggerName"] == "new_telegram_message"
    assert inb["trigger"]["nextAction"]["nextAction"]["settings"]["pieceName"] == flows.PIECE["telegram"]
    try:
        flows.build_flow("BOGUS", agent="a", thread_id="t", prompt="p")
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---- subscriptions -------------------------------------------------------
def test_subscription_store():
    st = subscriptions.SubscriptionStore(":memory:")
    sub = subscriptions.Subscription(id="s1", mode="CRON", target_agent="papers",
                                     backend="react", source_type="time", source_connector="cron",
                                     ap_flow_id="f1", deliver_to=["telegram"], thread_id="sub:1",
                                     prompt="arxiv", flow_name="ea:cron-papers-daily-ab12")
    st.upsert(sub)
    got = st.get("s1")
    assert got and got.mode == "CRON" and got.deliver_to == ["telegram"] and got.created_at > 0
    assert got.flow_name == "ea:cron-papers-daily-ab12"            # the readable AP flow name
    assert st.as_dicts()[0]["flow_name"] == "ea:cron-papers-daily-ab12"  # surfaced to the UI
    assert len(st.list()) == 1 and st.by_agent("papers")[0].id == "s1"
    st.set_status("s1", "paused")
    assert st.get("s1").status == "paused" and st.list(status="active") == []
    st.delete("s1")
    assert st.get("s1") is None


# ---- classify (heuristic baseline / eval oracle) -------------------------
def test_classify_clear_cases():
    cases = {
        "what's the bitcoin price right now?": "NOW",
        "geobot, capital of Japan?": "NOW",
        "every weekday at 9am send me IBM's price": "CRON",
        "every 2 minutes send me the time": "CRON",
        "check bitcoin every 30 minutes and message me only if it moves more than 3%": "POLL",
        "watch IBM and ping me only if it drops below 40": "POLL",
        "when a new file lands in my Box folder, summarize it": "PUSH",
        "when a PR opens, review it": "PUSH",
    }
    for text, want in cases.items():
        assert classify.classify(text) == want, f"{text!r} → {classify.classify(text)} != {want}"


def test_classify_cadence_and_source():
    assert classify.cadence_of("every 2 minutes")["interval_seconds"] == 120
    assert classify.cadence_of("every 30 minutes")["interval_seconds"] == 1800
    assert classify.cadence_of("daily at 9am")["cron"] == "0 9 * * *"
    assert classify.cadence_of("every weekday at 9am")["cron"] == "0 9 * * 1-5"
    # word-numbers (dictated/voice input) + unit-only intervals
    assert classify.cadence_of("every five minutes post about IBM stock")["interval_seconds"] == 300
    assert classify.cadence_of("every hour check the feed")["interval_seconds"] == 3600
    # the unit-only 'every day' must NOT eat an anchored time
    assert classify.cadence_of("every day at 9am send the brief")["cron"] == "0 9 * * *"


def test_classify_ttl_bounded_runs():
    """"… for one hour" is a BOUNDED run: ttl_of feeds expires_at at arm time and /invoke's
    expiry gate ends the flow. A cadence's own phrase ("every hour") must not read as a TTL."""
    assert classify.ttl_of("every five minutes post about IBM stock for one hour") == 3600
    assert classify.ttl_of("every 5 minutes check bitcoin for the next 2 hours") == 7200
    assert classify.ttl_of("every minute ping me for 30 minutes") == 1800
    assert classify.ttl_of("check the feed every hour") is None
    assert classify.ttl_of("watch bitcoin every 2 minutes and ping me on any move") is None
    assert classify.ttl_of("summarize new PRs") is None
    assert classify.source_of("when a file lands in Box") == ("box", "new_file")
    # canonical registry names now: (app, event) — the old ("github_pr", "new_pull_request")
    # sub-trigger labels resolve through triggers.SOURCE_ALIASES for legacy callers.
    assert classify.source_of("when a PR opens") == ("github", "new_pr")
    assert classify.source_of("when a release is published") == ("github", "new_release")
    assert classify.source_of("when I label an email 'Read-later'") == ("gmail", "new_labeled_email")
    d = classify.decision("when a resume lands in my Box folder, email me")
    assert d["mode"] == "PUSH" and d["source"] == "box"


# ---- StubRuntime (the AgentRuntime port) ---------------------------------
def test_stub_runtime_memory_and_reuse():
    r = runtime.StubRuntime()
    r.upsert_agent(runtime.AgentSpec(name="geobot", prompt="geo"))
    assert r.get_agent("geobot").prompt == "geo"
    assert [a.name for a in r.list_agents()] == ["geobot"]
    a1 = asyncio.run(r.run("geobot", "t1", "capital of Japan?"))
    a2 = asyncio.run(r.run("geobot", "t1", "and its population?"))
    a3 = asyncio.run(r.run("geobot", "t2", "capital of Peru?"))
    assert "turn#1" in a1 and "turn#2" in a2 and "turn#1" in a3   # per-thread memory
    try:
        asyncio.run(r.run("nope", "t", "x"))
        assert False, "expected KeyError"
    except KeyError:
        pass


# ---- concierge planner (reason -> build; the dry-run core) ---------------
def test_concierge_plan():
    now = concierge_plan.plan("what's the bitcoin price right now?", agent="pricebot")
    assert now["decision"]["mode"] == "NOW" and now["flow"] is None

    cron = concierge_plan.plan("every 2 minutes send me the time", agent="clockbot",
                               thread_id="gw:telegram:42")
    assert cron["decision"]["mode"] == "CRON"
    assert cron["flow"]["trigger"]["settings"]["input"]["minutes"] == 2
    assert cron["flow"]["trigger"]["nextAction"]["settings"]["input"]["body"]["agent"] == "clockbot"

    push = concierge_plan.plan("when a resume lands in my Box folder, email me the fit",
                               agent="resume_judge")
    assert push["decision"]["mode"] == "PUSH" and push["decision"]["source"] == "box"
    assert push["flow"]["trigger"]["settings"]["pieceName"] == flows.PIECE["box"]

    poll = concierge_plan.plan("watch BTC every 1 min and ping me only if it moves", agent="pricebot")
    assert poll["decision"]["mode"] == "POLL" and poll["flow"].get("__mode") == "poll"


# ---- eval oracle over the catalog (soft, reported) -----------------------
CATALOG = [  # (utterance, expected mode) — the event-agent-ap-inspired set
    ("what's the bitcoin price right now?", "NOW"),
    ("!ask what does the runbook say about DB failover?", "NOW"),
    ("send me the 3 latest arXiv papers on LLM agents", "NOW"),
    ("every weekday 8am post the overnight support digest to #support", "CRON"),
    ("every morning email me a summary of new CVs added to Box", "CRON"),
    ("each Friday 5pm WhatsApp me this week's closed GitHub issues", "CRON"),
    ("when a PR opens, review it and Slack me if risky", "PUSH"),
    ("when a customer email arrives, draft a reply and Slack me", "PUSH"),
    ("when a resume lands in the hiring folder, email me the fit", "PUSH"),
    ("check our status page every 5 min; alert #ops if a service goes down", "POLL"),
    ("watch IBM during market hours; Telegram me on a >2% move", "POLL"),
    ("watch this blog; DM me new posts mentioning 'acquisition'", "POLL"),
]


def test_eval_oracle_pass_rate():
    ok = sum(1 for text, want in CATALOG if classify.classify(text) == want)
    rate = ok / len(CATALOG)
    print(f"[eval] heuristic classifier: {ok}/{len(CATALOG)} = {rate:.0%}")
    for text, want in CATALOG:
        got = classify.classify(text)
        if got != want:
            print(f"       MISS want={want} got={got}: {text}")
    assert rate >= 0.75, f"heuristic baseline below 75%: {rate:.0%}"


# ---- plain-python3 runner ------------------------------------------------
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)
