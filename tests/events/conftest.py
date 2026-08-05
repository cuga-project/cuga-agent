"""Make the offline events suite HERMETIC — no matter what is running on this machine.

Several endpoints fire an inner HTTP call at ``127.0.0.1:$EVENTS_CUGA_PORT/invoke`` (the webhook
seam, the native scheduler, the debug-run endpoint). In a unit test nothing should answer that: the
assertions are about the ENVELOPE we send, not about an agent actually running.

Without this, whether the suite passed and how long it took depended on whether a dev stack happened
to be listening on :7860 / :8100. With the stack up, those calls reached a real server and made real
LLM calls — a 50-second suite took 25+ minutes and eventually timed out. That is a miserable failure
mode: it looks like a hang in your new code, and it comes and goes with something outside the repo.

So: point the loopback seams at a port nothing is bound to, for the whole session. Real network
behaviour is covered by the LIVE harnesses (tests/events/live_*.py), which target a running stack on
purpose.
"""

import os
import socket

import pytest


def _closed_port() -> str:
    """A port that was free at collection time — bind, read, release. Connections refuse instantly,
    which is exactly what we want: fast, deterministic, and obviously 'nothing is there'."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return str(s.getsockname()[1])
    finally:
        s.close()


@pytest.fixture(autouse=True, scope="session")
def _hermetic_loopback():
    dead = _closed_port()
    saved = {k: os.environ.get(k) for k in ("EVENTS_CUGA_PORT", "CUGA_URL", "EVENTS_API_URL")}
    os.environ["EVENTS_CUGA_PORT"] = dead
    os.environ["CUGA_URL"] = f"http://127.0.0.1:{dead}"
    os.environ.pop("EVENTS_API_URL", None)      # CUGA's slash forwarder: off unless a test sets it
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
