"""Offline unit tests across every Phase 1 & 2 dimension — the fast, no-network coverage.

Runs with **plain python3** (stdlib only) OR pytest. Loads the dependency-light events modules
directly (like ``test_events_core.py``), so no synced venv, no LLM, no AP, no network. Covers the
dimensions that ``test_events_core`` doesn't: **connectors** (channels/integrations status),
**catalog** (examples), **credentials** (shared vs per-user), **principal/isolation** (scope,
AP project grains), **flows** (per-mode builders), and **runtime selection** (cuga default +
fallback gating).

    python3 tests/events/test_events_dimensions.py
    uv run pytest tests/events/test_events_dimensions.py
"""

import os
import sys

_EVENTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "cuga", "backend", "events")
)
if _EVENTS not in sys.path:
    sys.path.insert(0, _EVENTS)

import connectors  # noqa: E402
import catalog  # noqa: E402
import credentials  # noqa: E402
import principal  # noqa: E402
import flows  # noqa: E402
import runtime  # noqa: E402
import mcp_catalog  # noqa: E402


# ---- connectors: channels (env-driven status) ----------------------------
def test_channels_status_env_driven():
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    ch = {c["name"]: c for c in connectors.channels_status()}
    assert ch["web"]["status"] == "connected"  # built-in, always on
    assert ch["telegram"]["status"] == "not_configured"  # no token
    os.environ["TELEGRAM_BOT_TOKEN"] = "x"
    try:
        ch2 = {c["name"]: c for c in connectors.channels_status()}
        assert ch2["telegram"]["status"] == "connected"  # token present
    finally:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)


# ---- connectors: integrations (AP-connection-driven status) --------------
def test_integrations_status_from_connections():
    # AP off → everything ap_not_configured
    off = {i["name"]: i for i in connectors.integrations_status(None, ap_configured=False)}
    assert off["gmail"]["status"] == "ap_not_configured"
    # AP on, no connections → not_connected
    none = {i["name"]: i for i in connectors.integrations_status([], ap_configured=True)}
    assert none["github"]["status"] == "not_connected"
    # AP on, a github connection present → connected (only github)
    conns = [{"externalId": "ea::acme::alice::github"}]
    got = {i["name"]: i for i in connectors.integrations_status(conns, ap_configured=True)}
    assert got["github"]["status"] == "connected"
    assert got["gmail"]["status"] == "not_connected"
    # AP on but connections unknown (AP down) → unknown, never crash
    unk = {i["name"]: i for i in connectors.integrations_status(None, ap_configured=True)}
    assert unk["box"]["status"] == "unknown"


# ---- catalog: examples across modes --------------------------------------
def test_catalog_examples():
    ex = catalog.as_list()
    assert len(ex) >= 7
    outcomes = {e["outcome"] for e in ex}
    # the router's outcomes are all represented in the examples
    assert {"answer-now", "flow-cron", "connect", "decline"} <= outcomes
    for e in ex:
        assert e["id"] and e["title"] and e["utterance"] and e["outcome"] and "live" in e


# ---- credentials: shared vs per-user resolution --------------------------
def test_credentials_shared_vs_per_user():
    alice = principal.Principal(tenant_id="acme", user_id="alice")
    bob = principal.Principal(tenant_id="acme", user_id="bob")
    # shared → same connection for everyone (keyed by agent, not user)
    sa = credentials.connection_external_id("telegram", "shared", alice, agent="mailbot")
    sb = credentials.connection_external_id("telegram", "shared", bob, agent="mailbot")
    assert sa == sb and "agent::mailbot" in sa
    # per-user → distinct per user
    pa = credentials.connection_external_id("telegram", "per-user", alice, agent="mailbot")
    pb = credentials.connection_external_id("telegram", "per-user", bob, agent="mailbot")
    assert pa != pb and "alice" in pa and "bob" in pb
    assert credentials.is_per_user("per-user") is True
    assert credentials.is_per_user("shared") is False


# ---- principal / isolation: scope + AP project grains --------------------
def test_principal_scope_and_grains():
    p = principal.Principal(tenant_id="acme", instance_id="inst", user_id="alice")
    assert p.scope == "acme/inst/alice"
    assert principal.DEFAULT.scope == "default/default/local"
    # AP project grain (tenant → per-(tenant,instance); shared → None; user → per-user)
    assert p.ap_project_name("shared") is None
    tenant_proj = p.ap_project_name("tenant")
    assert tenant_proj and tenant_proj.startswith("ea::") and "acme" in tenant_proj
    assert "alice" in p.ap_project_name("user")
    # connection externalId is user-scoped
    assert p.connection_external_id("gmail") == "ea::acme::alice::gmail"
    # thread namespacing keeps scope
    assert p.scope in p.thread("web:1")


# ---- flows: a builder per mode, all valid shapes -------------------------
def test_flow_builders_per_mode():
    cron = flows.build_cron_flow(agent="papers", cron="0 8 * * 1-5", thread_id="sub:1", prompt="brief")
    assert cron["trigger"] and cron["valid"]
    poll = flows.build_poll_flow(agent="pricebot", interval_seconds=120, thread_id="sub:2", prompt="watch")
    assert poll["trigger"]
    push = flows.build_push_flow(
        agent="resume_judge", source="box", event_kind="new_file", thread_id="sub:3", prompt="judge"
    )
    assert push["trigger"]
    # dispatcher covers each mode (with each mode's required kwargs)
    assert flows.build_flow("NOW", agent="a", thread_id="t", prompt="p")["trigger"]
    assert flows.build_flow("CRON", agent="a", thread_id="t", prompt="p", cron="* * * * *")["trigger"]
    assert flows.build_flow("POLL", agent="a", thread_id="t", prompt="p", interval_seconds=60)["trigger"]
    assert flows.build_flow("PUSH", agent="a", thread_id="t", prompt="p", source="box")["trigger"]


# ---- runtime selection: cuga default + fallback GATING -------------------
def test_runtime_selection_single_agent_world():
    """The single-agent world (events_docs/plans/SUPERVISOR_REFACTOR.md): one addressable agent,
    'cuga', in both modes. unset → ClassicRuntime (the plain agent, as main ships it);
    EVENTS_SUPERVISOR=1 → SupervisorRuntime (sub-agents from the canonical YAML roster).
    Neither accepts registrations — the fleet-routing runtime is retired."""
    import pytest

    os.environ.pop("EVENTS_SUPERVISOR", None)
    rt = runtime.make_runtime("cuga", app_context=lambda: None)
    assert isinstance(rt, runtime.ClassicRuntime)
    assert [a.name for a in rt.list_agents()] == ["cuga"]
    assert rt.get_agent("anything").name == "cuga"  # every id resolves to THE agent
    with pytest.raises(RuntimeError):  # no registrations in classic mode
        rt.upsert_agent(runtime.AgentSpec(name="x"))
    # explicit react (dev/test) unchanged
    assert isinstance(runtime.make_runtime("react", model_factory=lambda s: None), runtime.ReactRuntime)
    os.environ["EVENTS_SUPERVISOR"] = "1"
    try:
        rt2 = runtime.make_runtime("cuga")
        assert isinstance(rt2, runtime.SupervisorRuntime)
        assert rt2.get_agent("cuga") is not None
        with pytest.raises(RuntimeError):  # sub-agents come from the YAML only
            rt2.upsert_agent(runtime.AgentSpec(name="x"))
    finally:
        os.environ.pop("EVENTS_SUPERVISOR", None)


def test_cuga_storage_isolation_via_agentstore():
    from agent_store import AgentStore
    from runtime import CugaRuntime, AgentSpec

    rt = CugaRuntime(agent_store=AgentStore(":memory:"))
    rt.upsert_agent(
        AgentSpec(name="pricebot", prompt="x", mcp_servers=["cuga-finance"]), scope="acme/·/alice"
    )
    got = rt.get_agent("pricebot", scope="acme/·/alice")
    assert got is not None and got.backend == "cuga"  # tagged cuga
    assert rt.get_agent("pricebot", scope="acme/·/bob") is None  # isolated by scope


def test_cuga_run_raises_without_stack_and_no_fallback():
    import asyncio
    from agent_store import AgentStore
    from runtime import CugaRuntime, AgentSpec

    rt = CugaRuntime(agent_store=AgentStore(":memory:"), app_context=lambda: None)  # no ctx, no fb
    rt.upsert_agent(AgentSpec(name="w", prompt="x"), scope="s")
    raised = None
    try:
        asyncio.run(rt.run("w", "t", "hi", scope="s"))
    except RuntimeError as e:
        raised = str(e)
    assert raised and "CUGA worker backend requires" in raised, (
        f"expected the no-fallback RuntimeError, got: {raised!r}"
    )


# ---- mcp catalog: the full event-agent-ap set ----------------------------
def test_mcp_catalog_full_set():
    names = mcp_catalog.known_names()
    for app in ("web", "knowledge", "geo", "finance", "code", "local", "text"):
        assert f"cuga-{app}" in names  # all 7 event-agent-ap servers
    assert mcp_catalog.known_mcp_url("cuga-finance").endswith("/mcp")


# ---- seed: pre-built agents carry channels + integrations ----------------
def test_seed_agents_carry_connectors():
    import seed
    from agent_store import AgentStore
    from runtime import CugaRuntime

    rt = CugaRuntime(agent_store=AgentStore(":memory:"))
    names = seed.seed_default_agents(rt, scope="acme/·/alice", backend="cuga")
    assert "pricebot" in names and "mailbot" in names
    mail = rt.get_agent("mailbot", scope="acme/·/alice")
    assert any(i["app"] == "gmail" and i["ownership"] == "per-user" for i in mail.integrations)
    assert "telegram" in mail.channels
    # a different scope sees nothing (isolation preserved through seeding)
    assert rt.get_agent("mailbot", scope="acme/·/bob") is None
    # the three full-AP integrations each have a driving agent (drives per-user connect)
    assert "pr_reviewer" in names  # GitHub agent
    pr = rt.get_agent("pr_reviewer", scope="acme/·/alice")
    assert any(i["app"] == "github" for i in pr.integrations)
    assert "incident_triage" in names  # the generic webhook worker
    it = rt.get_agent("incident_triage", scope="acme/·/alice")
    # triage now ALSO owns trigger-grain watcher events (github issues, :bug: reactions, box
    # comments) — declarations carry a "triggers" list naming WHICH events of the app they handle.
    assert "slack" in it.channels
    assert any(i["app"] == "github" and "new_issue" in i.get("triggers", []) for i in it.integrations)
    # trigger-grain on pr_reviewer: PR-shaped events only (repo lifecycle lives on repo_watcher)
    assert any("new_pr" in i.get("triggers", []) for i in pr.integrations)
    assert "repo_watcher" in names  # github lifecycle watcher
    rw = rt.get_agent("repo_watcher", scope="acme/·/alice")
    assert any(i["app"] == "github" and "new_release" in i.get("triggers", []) for i in rw.integrations)
    rj = rt.get_agent("resume_judge", scope="acme/·/alice")
    assert {i["app"] for i in rj.integrations} >= {"box", "gmail"}  # Box + Gmail watcher


def test_integrations_full_ap_wiring():
    """Box · GitHub · Gmail are full-AP integrations: an OAuth/token provider, a PUSH source-trigger,
    a setup guide, and a status row. This is the 'integrations = AP' contract."""
    import oauth
    import setup_guides

    # oauth providers (Box/Gmail = OAuth; GitHub = token PAT)
    assert oauth.connect_kind("box") == "oauth" and oauth.connect_kind("gmail") == "oauth"
    # github is an OAUTH app: `@activepieces/piece-github` accepts only OAUTH2/CUSTOM_AUTH, never
    # SECRET_TEXT. Storing a PAT there yields a connection AP keeps and the piece cannot use.
    assert oauth.connect_kind("github") == "oauth"
    # AP piece PUSH triggers use the REAL piece trigger names (verified against the running pieces)
    assert flows.SOURCE_TRIGGER["box"] == ("box", "new_file")
    assert flows.SOURCE_TRIGGER["github_pr"] == ("github", "trigger_pull_request")
    assert flows.SOURCE_TRIGGER["gmail"] == ("gmail", "gmail_new_email_received")
    # a setup guide per integration, all AP-wired
    for app in ("box", "github", "gmail"):
        g = setup_guides.guide(app)
        assert g and g["kind"] == "integration" and "AP" in g["wiring"]
    # a push flow builder wires the piece trigger → /invoke for each source
    for src in ("box", "github_pr", "gmail"):
        f = flows.build_push_flow(agent="x", source=src, thread_id="t", prompt="p")
        assert f["trigger"]["settings"]["pieceName"].startswith("@activepieces/piece-")


# ---- flow dedup: reuse-or-create by identity -----------------------------
def test_flow_dedup_find_by_key():
    from subscriptions import SubscriptionStore, Subscription

    st = SubscriptionStore(":memory:")
    key = "papers|time|1m|telegram|acme"
    st.upsert(
        Subscription(
            id="s1",
            mode="CRON",
            target_agent="papers",
            tenant="acme/·/alice",
            deliver_to=["telegram"],
            dedup_key=key,
        )
    )
    assert st.find_by_dedup_key(key).id == "s1"  # matching identity → reuse
    assert st.find_by_dedup_key("papers|time|5m|telegram|acme") is None  # different cadence → new
    assert st.find_by_dedup_key("") is None


# ---- flow grain follows credentials --------------------------------------
def test_owner_scope_grain_follows_credentials():
    import concierge
    from runtime import AgentSpec

    p = principal.Principal(tenant_id="acme", instance_id="inst", user_id="alice")
    shared = AgentSpec(name="digest", integrations=[{"app": "slack", "ownership": "shared"}])
    peruser = AgentSpec(name="mailbot", integrations=[{"app": "gmail", "ownership": "per-user"}])
    none = AgentSpec(name="pricebot")
    assert concierge._owner_scope(shared, p) == p.tenant_id  # all shared → tenant-wide flow
    assert concierge._owner_scope(none, p) == p.tenant_id  # no integrations → tenant-wide
    assert concierge._owner_scope(peruser, p) == p.scope  # any per-user → per-user flow


# ---- cron/poll prompts are SINGLE-SHOT (the schedule owns recurrence) -----
def test_cron_poll_prompt_strips_cadence():
    """A scheduled agent runs ONCE per tick; a prompt still saying 'every 5 minutes'/'monitor'
    made the agent try to loop+sleep and hit the execution timeout. _strip_cadence removes the
    recurrence phrasing so each fire is a clean single check."""
    import concierge

    s = concierge._strip_cadence
    assert "every" not in s("monitor bitcoin every 5 minutes and notify me when it changes").lower()
    assert "monitor" not in s("monitor the bitcoin price every 5 minutes").lower()  # → "check"
    assert s("send me the weather every day at 9am").lower().strip() == "send me the weather"
    assert "hourly" not in s("check the feed hourly and summarize new items").lower()
    # a prompt with no cadence phrasing is unchanged in substance
    assert "review the PR" in s("review the PR")


def test_strip_cadence_covers_the_whole_example_corpus():
    """Every cron/poll utterance in the catalog must strip clean — the corpus is the regression
    net for the regex. A new example that leaks recurrence phrasing into the fired prompt fails
    HERE, not in a user's timed-out agent run."""
    import re
    import concierge
    import catalog  # flat import (events dir on sys.path, suite convention)

    cad = re.compile(r"\bevery\b|\bhourly\b|\bdaily\b|\bmonitor|\bkeep (an eye|watching|checking)", re.I)
    leaks = [
        e["utterance"]
        for e in catalog.EXAMPLES
        if e.get("trigger") in ("cron", "poll")
        and e.get("utterance")
        and cad.search(concierge._strip_cadence(e["utterance"]))
    ]
    assert not leaks, f"cadence survives stripping in: {leaks}"


def test_concierge_now_fastpath_skips_the_llm():
    """THE ROUTING RULE: a regular conversational message goes STRAIGHT to the worker — the
    concierge react agent (an extra LLM hop) must not even be BUILT. Standing intent (cron
    phrasing) still takes the LLM path; the kill switch restores the old behavior."""
    import asyncio
    import concierge as cz
    import runtime as rt_mod

    rt = rt_mod.StubRuntime()
    # register under the DEFAULT principal's agent scope (what the fast-path runs with)
    rt.upsert_agent(
        rt_mod.AgentSpec(name="cuga", prompt="the one agent"), scope=principal.DEFAULT.agent_scope
    )
    poisoned = {"called": False}

    def factory(_):
        poisoned["called"] = True
        raise RuntimeError("LLM must not be constructed for a NOW message")

    con = cz.Concierge(rt, model_factory=factory)
    # NOW → worker answer, no LLM build
    ans = asyncio.run(con.run("t1", "what is the capital of Japan?"))
    assert ans and "error" not in ans.lower() and poisoned["called"] is False
    # standing intent → the LLM path IS attempted (factory raises = proof of routing)
    try:
        asyncio.run(con.run("t1", "every day at 9am send me a market brief"))
        routed_to_llm = poisoned["called"]
    except RuntimeError:
        routed_to_llm = True
    assert routed_to_llm
    # kill switch: NOW goes back through the LLM path
    poisoned["called"] = False
    con2 = cz.Concierge(rt, model_factory=factory)
    os.environ["EVENTS_NOW_FASTPATH"] = "0"
    try:
        try:
            asyncio.run(con2.run("t2", "what is the capital of Japan?"))
        except RuntimeError:
            pass
        assert poisoned["called"] is True
    finally:
        os.environ.pop("EVENTS_NOW_FASTPATH", None)


def test_single_shot_task_llm_rewrite_with_regex_fallback():
    """The cadence stripper is an LLM rewrite at ARM time (one call per flow, never per tick);
    _strip_cadence is the guarded fallback. Four contracts: a clean LLM answer is used verbatim;
    a leaking answer is rejected (the leak regex) in favor of the regex; an LLM error falls back;
    EVENTS_CADENCE_LLM=0 never touches the LLM at all."""
    import asyncio
    import concierge

    class _Fake:
        def __init__(self, reply=None, err=None):
            self.reply, self.err, self.calls = reply, err, 0

        async def ainvoke(self, _msgs):
            self.calls += 1
            if self.err:
                raise self.err

            class R:  # noqa: D401 - minimal AIMessage stand-in
                content = self.reply

            return R()

    utt = "watch bitcoin every 5 minutes and ping me on any move"
    old = concierge._cadence_model
    try:
        # 1. clean rewrite → used verbatim
        concierge._cadence_model = _Fake("Check the current bitcoin price once and ping me if it moved.")
        out = asyncio.run(concierge._single_shot_task(utt))
        assert out.startswith("Check the current bitcoin price")
        # 2. cadence leaks through the LLM → rejected, regex fallback
        concierge._cadence_model = _Fake("keep watching bitcoin every 5 minutes")
        out = asyncio.run(concierge._single_shot_task(utt))
        assert out == concierge._strip_cadence(utt)
        # 3. LLM raises → regex fallback
        concierge._cadence_model = _Fake(err=RuntimeError("no api key"))
        out = asyncio.run(concierge._single_shot_task(utt))
        assert out == concierge._strip_cadence(utt)
        # 4. kill switch → LLM never invoked
        gate = _Fake("should never be called")
        concierge._cadence_model = gate
        os.environ["EVENTS_CADENCE_LLM"] = "0"
        try:
            out = asyncio.run(concierge._single_shot_task(utt))
        finally:
            os.environ.pop("EVENTS_CADENCE_LLM", None)
        assert out == concierge._strip_cadence(utt) and gate.calls == 0
    finally:
        concierge._cadence_model = old


# ---- oauth connect registry (CUGA hosts connect) -------------------------
def test_oauth_registry_and_authorize_url():
    import oauth

    assert oauth.connect_kind("gmail") == "oauth" and oauth.connect_kind("github") == "oauth"
    assert oauth.connect_kind("telegram") == "token"  # piece-telegram-bot: SECRET_TEXT
    assert oauth.connect_kind("nope") is None
    assert oauth.is_configured("telegram") is True  # token apps always connectable
    # the PR/issue trigger creates a repository webhook, which needs admin:repo_hook
    assert set(oauth.provider("github")["scopes"]) == {"repo", "admin:repo_hook"}
    assert oauth.provider("github")["auth"].startswith("https://github.com/login/oauth/")
    # oauth app: not configured until client id/secret present
    for k in ("EVENTS_OAUTH_GMAIL_CLIENT_ID", "EVENTS_OAUTH_GMAIL_CLIENT_SECRET"):
        os.environ.pop(k, None)
    assert oauth.is_configured("gmail") is False
    assert oauth.authorize_url("gmail", "st") is None
    os.environ["EVENTS_OAUTH_GMAIL_CLIENT_ID"] = "cid"
    os.environ["EVENTS_OAUTH_GMAIL_CLIENT_SECRET"] = "sec"
    try:
        assert oauth.is_configured("gmail") is True
        url = oauth.authorize_url("gmail", "st123")
        assert url and "accounts.google.com" in url and "client_id=cid" in url and "state=st123" in url
        assert oauth.redirect_uri("gmail").endswith("/api/events/connect/gmail/callback")
        assert oauth.decode_state(oauth.encode_state(scope="acme/·/alice", app="gmail"))["app"] == "gmail"
    finally:
        os.environ.pop("EVENTS_OAUTH_GMAIL_CLIENT_ID", None)
        os.environ.pop("EVENTS_OAUTH_GMAIL_CLIENT_SECRET", None)


# ---- users: local store + roles + auth -----------------------------------
def test_user_store():
    from users import UserStore

    us = UserStore(":memory:")
    us.add("alice", email="alice@acme.test", roles=["user"], password="pw1", tenant="acme")
    us.add("admin", email="admin@acme.test", roles=["admin", "builder", "user"], tenant="acme")
    assert us.get("alice", "acme").email == "alice@acme.test"
    assert us.by_email("admin@acme.test", "acme").has_role("admin")
    assert us.authenticate("alice@acme.test", "pw1", "acme").user_id == "alice"
    assert us.authenticate("alice@acme.test", "wrong", "acme") is None
    assert {u.user_id for u in us.list("acme")} == {"alice", "admin"}
    assert us.get("alice", "other-tenant") is None  # tenant-isolated


# ---- identity map: channel native id → user, + link tokens ---------------
def test_identity_map_and_link_tokens():
    from identity import IdentityMap

    im = IdentityMap(":memory:")
    im.link("acme", "telegram", "12345", "alice")
    assert im.resolve("acme", "telegram", "12345") == "alice"
    assert im.resolve("acme", "telegram", "99999") is None
    assert im.resolve("other", "telegram", "12345") is None  # tenant-isolated
    assert im.links_for_user("acme", "alice") == [{"channel": "telegram", "native_id": "12345"}]
    # link-token handshake (issued from the authenticated profile)
    tok = im.issue_token("acme", "bob", "telegram")
    assert im.redeem_token(tok, "67890") == "bob"  # binds bob's telegram id
    assert im.resolve("acme", "telegram", "67890") == "bob"
    assert im.redeem_token(tok, "67890") is None  # single-use
    assert im.redeem_token("garbage", "1") is None


# ---- per-agent permissions -----------------------------------------------
def test_perms_can_use_and_visible():
    import perms
    from runtime import AgentSpec

    open_agent = AgentSpec(name="pricebot")  # access [] → everyone
    restricted = AgentSpec(name="market_briefer", access=["builder", "admin", "alice"])
    assert perms.can_use(open_agent, roles=["user"]) is True
    assert perms.can_use(restricted, roles=["user"]) is False  # plain user denied
    assert perms.can_use(restricted, roles=["builder"]) is True
    assert perms.can_use(restricted, roles=["user"], user_id="alice") is True  # allow by user_id
    vis = perms.visible_agents([open_agent, restricted], roles=["user"])
    assert [a.name for a in vis] == ["pricebot"]  # user sees only the open one


# ---- principal: tenant agent_scope vs per-user scope + channel resolve ----
def test_principal_agent_scope_and_channel_resolve():
    from identity import IdentityMap

    a = principal.Principal(tenant_id="acme", instance_id="inst", user_id="alice")
    b = principal.Principal(tenant_id="acme", instance_id="inst", user_id="bob")
    assert a.agent_scope == "acme/inst" == b.agent_scope  # agents shared across users
    assert a.scope != b.scope  # run-state per user
    im = IdentityMap(":memory:")
    im.link("acme", "telegram", "12345", "alice")
    p = principal.resolve_channel("telegram", "12345", im, tenant_id="acme", instance_id="inst")
    assert p is not None and p.user_id == "alice" and p.scope == "acme/inst/alice"
    assert principal.resolve_channel("telegram", "nope", im, tenant_id="acme") is None


# ---- channels + box: inbound flows via descriptors, resume watcher -------
def test_channel_inbound_flows_and_box_watcher():
    # every channel builds from the CHANNELS descriptor — no per-channel code
    # action names VERIFIED against live AP piece metadata (telegram-bot@0.6.4, discord@0.5.3,
    # slack@0.17.2) — keep in sync with flows.CHANNELS.
    for ch, send in (
        ("telegram", "send_text_message"),
        ("discord", "sendMessageWithBot"),
        ("slack", "send_channel_message"),
    ):
        f = flows.build_inbound_flow(channel=ch, agent="concierge")
        trig = f["trigger"]
        assert trig["settings"]["pieceName"] == flows.PIECE[ch]
        # trigger → /invoke → send
        send_step = trig["nextAction"]["nextAction"]
        assert send_step["settings"]["actionName"] == send
        # the sender's native id rides in the /invoke thread_id (gw:<ch>:<native>)
        assert f"gw:{ch}:" in trig["nextAction"]["settings"]["input"]["body"]["source"]["thread_id"]


def test_discord_channel_wiring():
    """Discord specifics (verified vs discord@0.5.3): POLLING trigger over ONE channel, replies go
    to the message's channel_id (so THREAD messages reply in-thread), DROPDOWNs are DYNAMIC."""
    import oauth

    d = flows.CHANNELS["discord"]
    # reply target = the channel the message came from (NOT the author) → thread-safe
    assert d["native_ref"] == "{{trigger.channel_id}}"
    assert d["target_arg"] == "channel_id" and d["send_action"] == "sendMessageWithBot"
    assert d["text_arg"] == "message"
    # the polling trigger needs the channel id at arm time; channel dropdowns must be DYNAMIC
    assert d["trigger_args"] == ["channel"]
    assert "channel" in d["dynamic_props"] and "channel_id" in d["dynamic_props"]
    # the built inbound flow: /invoke thread_id + send both key off channel_id (in-thread replies)
    f = flows.build_inbound_flow(channel="discord", agent="concierge")
    step1 = f["trigger"]["nextAction"]
    assert step1["settings"]["input"]["body"]["source"]["thread_id"] == "gw:discord:{{trigger.channel_id}}"
    send = step1["nextAction"]
    assert send["settings"]["input"]["channel_id"] == "{{trigger.channel_id}}"
    # discord is a connectable token (Bot Token) app
    assert oauth.connect_kind("discord") == "token"
    assert oauth.provider("discord")["piece"].endswith("piece-discord")


def test_slack_direct_module():
    """The DEFAULT Slack backend is direct (no AP): filter bot/edit messages, verify signatures."""
    import slack_direct

    # should_process: answer real human messages; skip bot posts, edits/joins (subtype), empties
    assert slack_direct.should_process({"type": "message", "text": "hi", "channel": "C1"})
    assert not slack_direct.should_process({"type": "message", "text": "hi", "channel": "C1", "bot_id": "B1"})
    assert not slack_direct.should_process(
        {"type": "message", "text": "x", "channel": "C1", "subtype": "message_changed"}
    )
    assert not slack_direct.should_process({"type": "message", "channel": "C1"})  # no text
    assert not slack_direct.should_process({"type": "reaction_added"})
    # signature: no secret → allowed-but-flagged; wrong sig with a secret → rejected
    ok, why = slack_direct.verify_signature({}, "body")
    assert ok and "unverified" in why
    os.environ["SLACK_SIGNING_SECRET"] = "shh"
    try:
        bad, _ = slack_direct.verify_signature(
            {"x-slack-request-timestamp": "1", "x-slack-signature": "v0=deadbeef"}, "body"
        )
        assert bad is False
    finally:
        del os.environ["SLACK_SIGNING_SECRET"]


def test_slack_thread_scoping():
    """Per-thread Slack: the ``#<ts>`` suffix keys conversation memory per-thread, but
    channel_native_id strips it so identity/delivery stay channel-scoped; send_message threads the
    reply. Discord is already on this model (channel_id = the thread)."""
    import asyncio
    import slack_direct
    from envelope import Source

    # native id strips the #<ts> memory suffix; plain ids (Discord/Telegram) untouched
    assert principal.channel_native_id(Source(name="slack", thread_id="gw:slack:C1#1699.9")) == "C1"
    assert principal.channel_native_id(Source(name="slack", thread_id="gw:slack:C1")) == "C1"
    assert principal.channel_native_id(Source(name="discord", thread_id="gw:discord:42")) == "42"

    # channel_origin resolves the DELIVERY sink (channel, native) from a thread_id — tolerating a
    # scope prefix, which a PUSH flow always has (its source is the integration, not the channel).
    assert principal.channel_origin("gw:slack:C1#1699.9") == ("slack", "C1")  # channel reply
    assert principal.channel_origin("default/default/admin::gw:slack:C1#9.9") == ("slack", "C1")  # push
    assert principal.channel_origin("scope::gw:discord:42") == ("discord", "42")
    assert principal.channel_origin("web:studio") is None  # not a gw thread

    sent = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"ok": True}

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            sent.clear()
            sent.update(json or {})
            return _Resp()

    import httpx

    orig = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **k: _C()
    os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"
    try:
        asyncio.run(slack_direct.send_message("C1", "hi", thread_ts="1699.9"))
        assert sent.get("channel") == "C1" and sent.get("thread_ts") == "1699.9"  # reply in-thread
        asyncio.run(slack_direct.send_message("C1", "hi"))
        assert "thread_ts" not in sent  # root → no thread
    finally:
        httpx.AsyncClient = orig
        os.environ.pop("SLACK_BOT_TOKEN", None)


def test_channel_author_identity():
    """Per-user identity through a SHARED bot: source.user (the author) drives identity, falling back
    to the thread-native id. Discord/Slack inbound flows forward the author; Telegram keeps chat.id."""
    from envelope import Source

    # channel_user_id prefers the explicit author over the thread-native id
    assert (
        principal.channel_user_id(Source(name="slack", thread_id="gw:slack:C1", user="U_ALICE")) == "U_ALICE"
    )
    assert principal.channel_user_id(Source(name="slack", thread_id="gw:slack:C1")) == "C1"  # fallback
    assert principal.channel_user_id(Source(name="telegram", thread_id="gw:telegram:555")) == "555"
    # inbound flow templates forward the author: discord author id, slack user; telegram has none
    assert flows.CHANNELS["discord"]["user_ref"] == "{{trigger.author.id}}"
    assert flows.CHANNELS["slack"]["user_ref"] == "{{trigger.user}}"
    assert "user_ref" not in flows.CHANNELS["telegram"]
    df = flows.build_inbound_flow(channel="discord", agent="concierge")
    src = df["trigger"]["nextAction"]["settings"]["input"]["body"]["source"]
    assert src["user"] == "{{trigger.author.id}}" and src["thread_id"] == "gw:discord:{{trigger.channel_id}}"
    tf = flows.build_inbound_flow(channel="telegram", agent="concierge")
    assert "user" not in tf["trigger"]["nextAction"]["settings"]["input"]["body"]["source"]


def test_slack_channel_wiring():
    """Slack specifics (verified vs slack@0.17.2): APP_WEBHOOK trigger (Events API → instant),
    replies to the message's channel, requires ignoreBots + sendAsBot, channel DROPDOWN → DYNAMIC."""
    d = flows.CHANNELS["slack"]
    assert d["native_ref"] == "{{trigger.channel}}"  # reply to the channel, not the user
    assert d["send_action"] == "send_channel_message" and d["target_arg"] == "channel"
    assert d["text_arg"] == "text"
    assert d["const"].get("sendAsBot") is True  # required by send_channel_message
    assert d["trigger_const"].get("ignoreBots") is True  # required by new-message; skips bots
    assert "channel" in d["dynamic_props"]  # DROPDOWN fed a template
    f = flows.build_inbound_flow(channel="slack", agent="concierge")
    step1 = f["trigger"]["nextAction"]
    assert step1["settings"]["input"]["body"]["source"]["thread_id"] == "gw:slack:{{trigger.channel}}"
    assert step1["nextAction"]["settings"]["input"]["channel"] == "{{trigger.channel}}"


def test_delivery_backend_selection():
    """delivery.channel_backend: Slack + Discord + Telegram all default DIRECT (CUGA sends);
    EVENTS_<CH>_BACKEND overrides. is_direct drives whether a flow gets an AP send step."""
    import delivery

    assert delivery.is_direct("slack") and delivery.channel_backend("slack") == "direct"
    assert delivery.is_direct("discord") and delivery.channel_backend("discord") == "direct"
    assert delivery.is_direct("telegram") and delivery.channel_backend("telegram") == "direct"
    assert delivery.channel_backend("unknown_ch") == "ap"  # unknown → AP
    os.environ["EVENTS_DISCORD_BACKEND"] = "ap"  # env override → back to AP
    os.environ["EVENTS_TELEGRAM_BACKEND"] = "ap"  # …and telegram → AP
    try:
        assert not delivery.is_direct("discord")
        assert not delivery.is_direct("telegram")
    finally:
        del os.environ["EVENTS_DISCORD_BACKEND"]
        del os.environ["EVENTS_TELEGRAM_BACKEND"]


def test_discord_direct_module():
    """Direct Discord (Gateway) filtering: answer real human messages; skip bot/empty."""
    import discord_direct

    assert discord_direct.should_process(
        {"content": "hi", "channel_id": "C", "author": {"id": "U", "bot": False}}
    )
    assert not discord_direct.should_process(
        {"content": "hi", "channel_id": "C", "author": {"id": "B", "bot": True}}
    )
    assert not discord_direct.should_process({"channel_id": "C", "author": {"id": "U"}})  # no content
    assert not discord_direct.should_process({"content": "hi", "author": {"id": "U"}})  # no channel
    # intents bitmask includes MESSAGE_CONTENT (1<<15)
    assert discord_direct.INTENTS & (1 << 15)


def test_discord_mention_gate():
    """EVENTS_DISCORD_CHAT=mention: only @bot messages, DMs, and replies-to-the-bot reach CHAT
    (mirrors the Slack gate; watchers are dispatched by the caller on gated traffic)."""
    import discord_direct as dd

    old_id, old_env = dd._BOT_ID["id"], os.environ.get("EVENTS_DISCORD_CHAT")
    try:
        dd._BOT_ID["id"] = "BOT1"
        os.environ["EVENTS_DISCORD_CHAT"] = "mention"
        guild = {"guild_id": "G", "content": "hello there", "channel_id": "C"}
        assert dd.mention_gate(guild) == (False, "hello there")  # plain channel msg → gated
        ok, txt = dd.mention_gate({**guild, "content": "<@BOT1> price of btc", "mentions": [{"id": "BOT1"}]})
        assert ok and txt == "price of btc"  # mention → chat, stripped
        assert dd.mention_gate({**guild, "content": "<@!BOT1> hi"})[0]  # nickname-mention form
        assert dd.mention_gate({"content": "dm text", "channel_id": "D"})[0]  # DM (no guild_id)
        assert dd.mention_gate({**guild, "referenced_message": {"author": {"id": "BOT1"}}})[
            0
        ]  # reply to the bot
        os.environ["EVENTS_DISCORD_CHAT"] = "all"
        assert dd.mention_gate(guild)[0]  # default mode: everything
        dd._BOT_ID["id"] = ""
        os.environ["EVENTS_DISCORD_CHAT"] = "mention"
        assert dd.mention_gate(guild)[0]  # no READY yet → fail open
    finally:
        dd._BOT_ID["id"] = old_id
        (
            os.environ.__setitem__
            if old_env is not None
            else lambda *a: os.environ.pop("EVENTS_DISCORD_CHAT", None)
        )("EVENTS_DISCORD_CHAT", old_env)


# ---- runner --------------------------------------------------------------
if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
