"""`load_supervisor_config` must not change policy behaviour for callers that did not ask.

WHY THIS EXISTS. `load_supervisor_config` is public API — `CugaSupervisor.from_yaml()` (documented
in the README) and `cuga_graph/graph.py` both call it. For a while this branch hardcoded
`auto_load_policies=False` there, which silently disabled policy auto-loading for every downstream
supervisor user regardless of their `settings.policy.auto_load_policies`, with nothing raised and
nothing logged. These tests pin the contract:

    caller passes nothing  → per-agent None → CugaAgent falls back to settings   (UNCHANGED)
    caller passes False    → headless: no approval interrupt can hang the run
    YAML sets the key      → the YAML always wins

The events layer's supervisor is headless, so `server/main.py` asks for False explicitly.

Lives HERE, beside test_supervisor_yaml_config.py, rather than in tests/events: it must import the
real `cuga.sdk` to patch CugaAgent, and doing that inside the hermetic events suite polluted global
state and broke 18 unrelated tests.
"""

import textwrap

import pytest

pytest.importorskip("cuga.supervisor_utils.supervisor_config")

from cuga.supervisor_utils import supervisor_config as sc  # noqa: E402


class _FakeAgent:
    """Captures what load_supervisor_config passed, without building a real CugaAgent."""

    last = {}

    def __init__(self, **kw):
        _FakeAgent.last = dict(kw)
        type(self).captured.append(dict(kw))

    captured = []


@pytest.fixture()
def roster(tmp_path):
    p = tmp_path / "roster.yaml"
    p.write_text(textwrap.dedent("""
        supervisor:
          name: cuga
        agents:
        - name: plain
          special_instructions: no policy key at all
        - name: opted_in
          special_instructions: explicitly wants policies
          auto_load_policies: true
        - name: opted_out
          special_instructions: explicitly refuses policies
          auto_load_policies: false
    """).strip())
    return str(p)


@pytest.fixture(autouse=True)
def stub(monkeypatch):
    """Stub CugaAgent + tool loading so these stay unit tests (no model, no MCP)."""
    _FakeAgent.captured = []
    import cuga.sdk as sdk

    monkeypatch.setattr(sdk, "CugaAgent", _FakeAgent, raising=False)

    async def _no_tools(_cfg):
        return []

    async def _no_provider(**_kw):
        return None

    monkeypatch.setattr(sc, "_load_tools_from_config", _no_tools, raising=False)
    monkeypatch.setattr(sc, "_create_tool_provider", _no_provider, raising=False)
    monkeypatch.setattr(sc, "_get_model_from_config", lambda _c: None, raising=False)
    return _FakeAgent


def _by_name(roster_path, **kw):
    import asyncio

    asyncio.run(sc.load_supervisor_config(roster_path, **kw))
    names = ["plain", "opted_in", "opted_out"]
    return {n: c.get("auto_load_policies") for n, c in zip(names, _FakeAgent.captured)}


def test_default_caller_preserves_upstream_behaviour(roster):
    """No argument → None → CugaAgent falls back to settings.policy.auto_load_policies.

    This is THE regression guard: a hardcoded False here silently broke CugaSupervisor.from_yaml.
    """
    got = _by_name(roster)
    assert got["plain"] is None, "an agent with no key must defer to settings, not be forced off"


def test_headless_caller_can_default_it_off(roster):
    got = _by_name(roster, auto_load_policies=False)
    assert got["plain"] is False


def test_yaml_always_wins_over_the_caller_default(roster):
    got = _by_name(roster, auto_load_policies=False)
    assert got["opted_in"] is True, "an explicit YAML true must survive a headless caller"
    assert got["opted_out"] is False


def test_yaml_opt_out_survives_the_default_caller(roster):
    got = _by_name(roster)
    assert got["opted_out"] is False
    assert got["opted_in"] is True


def test_signature_is_keyword_only_and_optional():
    """Positional use would break existing callers; the param must be keyword-only with a default."""
    import inspect

    sig = inspect.signature(sc.load_supervisor_config)
    p = sig.parameters["auto_load_policies"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY
    assert p.default is None
    assert list(sig.parameters)[0] == "yaml_path"     # unchanged positional contract
