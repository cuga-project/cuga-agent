"""Stateful native POLL — the delta gate (Phase 3, Tiers 0–2).

Pure unit tests for the decision core, the SIGNAL protocol, spec extraction (heuristic path), and the
store's watch_state round-trip. No network, no server.
"""
import os
import sys

import pytest

HERE = os.path.dirname(__file__)
EVENTS = os.path.abspath(os.path.join(HERE, "..", "..", "src", "cuga", "backend", "events"))
if EVENTS not in sys.path:
    sys.path.insert(0, EVENTS)

import poll_state as ps                     # noqa: E402
from subscriptions import Subscription, SubscriptionStore  # noqa: E402


# ── SIGNAL protocol ──────────────────────────────────────────────────────────────────────────────
def test_parse_signal_strips_marker_and_returns_dict():
    clean, sig = ps.parse_signal("IBM is at 189.\n<<SIGNAL {\"value\": 189.2}>>")
    assert clean == "IBM is at 189."
    assert sig == {"value": 189.2}


def test_parse_signal_no_marker_is_passthrough():
    clean, sig = ps.parse_signal("nothing to see")
    assert clean == "nothing to see" and sig is None


def test_parse_signal_bad_json_strips_but_none():
    clean, sig = ps.parse_signal("hi <<SIGNAL {not json}>> there")
    assert "SIGNAL" not in clean and sig is None


def test_augment_prompt_per_kind():
    assert "value" in ps.augment_prompt({"kind": "threshold", "value_path": "IBM price"})
    assert "keys" in ps.augment_prompt({"kind": "identity"})
    fz = ps.augment_prompt({"kind": "fuzzy", "seen_keys": ["sunny 20C"]})
    assert "changed" in fz and "sunny 20C" in fz


# ── Tier 1: threshold ─────────────────────────────────────────────────────────────────────────────
def _ws(**kw):
    base = {"subscription_id": "s1", "kind": "fuzzy", "seen_keys": [], "baseline": None,
            "reset_policy": "ratchet", "value_path": "", "threshold": 0.0, "updated_at": 0.0}
    base.update(kw)
    return base


def test_threshold_first_tick_seeds_baseline_no_alert():
    d = ps.decide(_ws(kind="threshold", threshold=0.05), {"value": 100}, now=1.0)
    assert d.changed is False and d.state["baseline"] == 100.0


def test_threshold_small_move_no_alert():
    d = ps.decide(_ws(kind="threshold", threshold=0.05, baseline=100.0), {"value": 103}, now=1.0)
    assert d.changed is False and d.state["baseline"] == 100.0    # ratchet keeps baseline until it fires


def test_threshold_big_move_alerts_and_ratchets():
    d = ps.decide(_ws(kind="threshold", threshold=0.05, baseline=100.0), {"value": 106}, now=1.0)
    assert d.changed is True and d.state["baseline"] == 106.0     # re-baselined to the alert value


def test_threshold_absolute_policy_keeps_fixed_baseline():
    d = ps.decide(_ws(kind="threshold", threshold=0.05, baseline=100.0, reset_policy="absolute"),
                  {"value": 130}, now=1.0)
    assert d.changed is True and d.state["baseline"] == 100.0     # never moves


def test_threshold_per_tick_rebaselines_even_without_alert():
    d = ps.decide(_ws(kind="threshold", threshold=0.10, baseline=100.0, reset_policy="per_tick"),
                  {"value": 103}, now=1.0)
    assert d.changed is False and d.state["baseline"] == 103.0    # follows every tick


def test_threshold_non_numeric_signal_no_alert():
    d = ps.decide(_ws(kind="threshold", threshold=0.05, baseline=100.0), {"value": "n/a"}, now=1.0)
    assert d.changed is False


# ── Tier 1: identity ───────────────────────────────────────────────────────────────────────────────
def test_identity_new_keys_alert_and_accumulate():
    d = ps.decide(_ws(kind="identity", seen_keys=["a", "b"]), {"keys": ["b", "c"]}, now=1.0)
    assert d.changed is True                                      # 'c' is new
    assert set(d.state["seen_keys"]) == {"a", "b", "c"}


def test_identity_no_new_keys_no_alert():
    d = ps.decide(_ws(kind="identity", seen_keys=["a", "b"]), {"keys": ["a"]}, now=1.0)
    assert d.changed is False


def test_identity_accepts_value_alias_and_caps():
    big = [str(i) for i in range(ps._SEEN_CAP + 50)]
    d = ps.decide(_ws(kind="identity", seen_keys=[]), {"value": big}, now=1.0)
    assert d.changed is True and len(d.state["seen_keys"]) <= ps._SEEN_CAP


# ── Tier 2: fuzzy ────────────────────────────────────────────────────────────────────────────────
def test_fuzzy_trusts_agent_verdict_and_stores_state():
    d = ps.decide(_ws(kind="fuzzy"), {"changed": True, "state": "rain expected"}, now=1.0)
    assert d.changed is True and d.state["seen_keys"] == ["rain expected"]


def test_fuzzy_unchanged():
    d = ps.decide(_ws(kind="fuzzy", seen_keys=["sunny"]), {"changed": False, "state": "sunny"}, now=1.0)
    assert d.changed is False


def test_none_signal_never_alerts():
    for kind in ("threshold", "identity", "fuzzy"):
        d = ps.decide(_ws(kind=kind, baseline=1.0), None, now=1.0)
        assert d.changed is False


# ── spec extraction (heuristic path; LLM off) ──────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    monkeypatch.setenv("EVENTS_POLL_LLM", "0")


@pytest.mark.asyncio
async def test_extract_spec_threshold_from_percent():
    spec = await ps.extract_spec("ping me if IBM stock moves by 5%")
    assert spec["kind"] == "threshold" and abs(spec["threshold"] - 0.05) < 1e-9


@pytest.mark.asyncio
async def test_extract_spec_identity_from_new():
    spec = await ps.extract_spec("tell me when a new file lands in /reports")
    assert spec["kind"] == "identity"


@pytest.mark.asyncio
async def test_extract_spec_defaults_fuzzy():
    spec = await ps.extract_spec("check the weather and let me know if it looks different")
    assert spec["kind"] == "fuzzy"


# ── store round-trip ───────────────────────────────────────────────────────────────────────────────
def test_watch_state_store_roundtrip_and_cascade_delete():
    st = SubscriptionStore(":memory:")
    st.upsert(Subscription(id="s1", mode="POLL", target_agent="cuga", backend="native"))
    assert st.get_watch_state("s1") is None                       # none until seeded
    st.set_watch_state(ps.spec_to_state("s1", {"kind": "threshold", "threshold": 0.05}))
    ws = st.get_watch_state("s1")
    assert ws["kind"] == "threshold" and ws["threshold"] == 0.05 and ws["seen_keys"] == []
    # update persists
    ws["baseline"] = 100.0
    st.set_watch_state(ws)
    assert st.get_watch_state("s1")["baseline"] == 100.0
    # deleting the subscription cascades
    st.delete("s1")
    assert st.get_watch_state("s1") is None
