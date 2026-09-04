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

The same hazard applies to the DATABASE and the run log, which the machine's ``.env`` can point at
real infrastructure — see ``_isolated_store`` below.
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
    keys = ("EVENTS_CUGA_PORT", "CUGA_URL", "EVENTS_API_URL")
    saved = {k: os.environ.get(k) for k in keys}
    os.environ["EVENTS_CUGA_PORT"] = dead
    os.environ["CUGA_URL"] = f"http://127.0.0.1:{dead}"
    os.environ.pop("EVENTS_API_URL", None)  # CUGA's slash forwarder: off unless a test sets it
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Give every test its own database and run-log directory.

    Two hazards, both invisible until they bite:

    * ``cuga.config`` calls ``load_dotenv`` at import, so a developer's ``.env`` — where
      ``EVENTS_DB`` is normally a real PostgreSQL DSN for local dev — is in the environment by the
      time the suite runs. ``register_events_routes`` mounts the mailbox and the NOW-run store from
      it, so the offline tests bind to whatever database that machine has. When that server is down
      the web-inbox tests fail with a connection error unrelated to the code under test. CI never
      sees it, because CI has no ``.env``. Note this must SET a value rather than unset one:
      ``load_dotenv(override=False)`` fills an *absent* variable back in, so popping it does not hold.
    * The run log defaults to ``<repo>/results/run_logs``, a real directory that accumulates across
      tests and across runs. It is gitignored, so it never appears in ``git status``.

    Function-scoped on purpose: the stores are built per ``register_events_routes`` call, so a fresh
    path per test is what keeps one test's NOW-runs out of the next one's ``/api/events/runs``.
    Postgres coverage is opt-in behind ``EVENTS_TEST_PG_DSN`` (test_db_postgres.py), untouched here.
    """
    monkeypatch.setenv("EVENTS_DB", str(tmp_path / "events.db"))
    monkeypatch.setenv("EVENTS_RUN_LOG_DIR", str(tmp_path / "run_logs"))
    # The offline suite builds apps with no GATEWAY_TOKEN / EVENTS_WEBHOOK_KEY / SLACK_SIGNING_SECRET
    # and asserts on behaviour BEHIND those gates. Those gates now fail CLOSED (an unset secret
    # refuses rather than waves everything through), so the suite has to declare what it is: a local
    # dev environment. This is the same switch an operator would use, not a test-only bypass — which
    # is the point, because it means the tests exercise the real code path.
    #
    # Tests that assert the CLOSED behaviour delete this themselves (test_fail_closed.py).
    monkeypatch.setenv("EVENTS_ALLOW_UNAUTHENTICATED", "1")


@pytest.fixture
def closed_gates(monkeypatch):
    """Opt OUT of the suite-wide dev switch, for tests that assert a gate actually REFUSES.

    `_isolated_store` sets EVENTS_ALLOW_UNAUTHENTICATED=1 for the whole suite, because the offline
    tests build apps with no secrets and assert on what is behind the gates. A test about the gate
    itself needs the opposite, and saying so per-test keeps that visible rather than depending on
    ordering.
    """
    monkeypatch.delenv("EVENTS_ALLOW_UNAUTHENTICATED", raising=False)
    return monkeypatch
