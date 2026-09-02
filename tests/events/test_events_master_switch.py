"""``CUGA_EVENTS_ENABLED`` gates every events-facing seam in CUGA core.

WHY THIS EXISTS
---------------
Each seam used to switch itself on from whichever variable it happened to need:

    /run + /run/agents mounted   because GATEWAY_TOKEN or CUGA_SUPERVISOR_ROSTER was set
    the roster was imported      because CUGA_SUPERVISOR_ROSTER was set
    `/automate …` was forwarded  because EVENTS_API_URL was set

Those variables get set for other reasons — GATEWAY_TOKEN is a generic shared secret, a roster path
can be left over from an experiment — so eventing could switch on as a side effect of unrelated
configuration, and there was nowhere to say "not on this instance". An always-mounted endpoint that
executes an agent is not something to acquire by accident.

So there is now one explicit opt-in, and these tests hold it: with the switch off, none of the three
seams activate no matter what else is configured.

This is NOT the old ``EVENTS_ENABLED``, which gated mounting the events layer INSIDE CUGA's process
and went away with combined mode. This gates core's seams towards a separate events service.
"""

from __future__ import annotations

import pytest

from cuga.backend.server import events_bridge

pytestmark = pytest.mark.unit

# This file imports ONLY events_bridge, which is a small module with no global state. Pulling in
# `run_routes` (and through it the whole CUGA server and its `settings` singleton) from a suite that
# blanks the environment poisoned unrelated tests further down the run — the singleton captures
# whatever the environment looked like at first import. The /run mounting half of the switch is
# therefore tested in test_split_service.py, which already builds a real client properly.


@pytest.fixture
def gates(monkeypatch):
    """Neutralise every gate variable for ONE test, then hand back monkeypatch to set what it needs.

    Two deliberate choices here, both learned the hard way:

    * EMPTY, not deleted. `cuga.config` runs `load_dotenv(override=False)` on import, which refills
      any variable that is currently ABSENT — so a deleted var comes back from the developer's own
      `.env` as soon as a later test imports something. Empty reads as "off" everywhere these are
      consumed, and being present means the refill cannot fire.
    * Opt-in, not autouse. Blanking six variables for every test in the file leaked into unrelated
      suites that ran afterwards and failed three of them. A fixture each test asks for keeps the
      blast radius to the test that wants it.
    """
    for var in (
        "CUGA_EVENTS_ENABLED",
        "GATEWAY_TOKEN",
        "CUGA_RUN_TOKEN",
        "CUGA_SUPERVISOR_ROSTER",
        "CUGA_RUN_ALLOW_UNAUTHENTICATED",
        "EVENTS_API_URL",
    ):
        monkeypatch.setenv(var, "")
    return monkeypatch


# ── the switch itself ─────────────────────────────────────────────────────────
def test_off_by_default(gates):
    """The whole point: a CUGA nobody configured for eventing has none of it."""
    assert events_bridge.events_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " true "])
def test_accepts_the_usual_spellings(gates, value):
    gates.setenv("CUGA_EVENTS_ENABLED", value)
    assert events_bridge.events_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "maybe"])
def test_anything_else_is_off(gates, value):
    """Fails CLOSED: an unrecognised value is off, not on."""
    gates.setenv("CUGA_EVENTS_ENABLED", value)
    assert events_bridge.events_enabled() is False


def test_an_inline_comment_does_not_leak_in(gates):
    """`.env` files carry `VALUE  # why`, and every other reader here strips it. If this one did
    not, "true # on for the demo" would be unrecognised and silently turn eventing OFF."""
    gates.setenv("CUGA_EVENTS_ENABLED", "true  # on for the demo box")
    assert events_bridge.events_enabled() is True


# ── the slash forwarder ───────────────────────────────────────────────
def test_slash_is_not_forwarded_when_the_switch_is_off(gates):
    """With eventing off, `/automate …` is ordinary text for the agent to answer — not a call out
    to a service this instance is not part of."""
    gates.setenv("EVENTS_API_URL", "http://localhost:8100")
    assert events_bridge.forwards_to_events("/automate every 5 minutes ping me", "web:1") is False


def test_slash_is_forwarded_when_both_are_set(gates):
    gates.setenv("CUGA_EVENTS_ENABLED", "1")
    gates.setenv("EVENTS_API_URL", "http://localhost:8100")
    assert events_bridge.forwards_to_events("/automate every 5 minutes ping me", "web:1") is True


def test_the_switch_alone_does_not_forward(gates):
    """Nowhere to forward to — the URL is still required."""
    gates.setenv("CUGA_EVENTS_ENABLED", "1")
    assert events_bridge.forwards_to_events("/automate ping me", "web:1") is False
