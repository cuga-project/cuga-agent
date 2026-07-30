"""CodeWrapper time-freeze: wall-clock frozen, but asyncio-safe.

In benchmark mode wrap_code freezes the wall clock (datetime/date/time module)
to the task datetime so the agent's code sees AppWorld's frozen time. It must
NOT freeze time.monotonic/perf_counter — asyncio's event loop uses monotonic
for every timer, so freezing it makes any `await` inside user code (e.g. the
LLM-backed find_tools) hang until the sandbox timeout. These checks pin both
halves: the clock is frozen AND asyncio still works.

Loaded directly (not via the cuga package) to stay fast/import-light.
"""

import asyncio
import calendar
import datetime as dtmod
import importlib.util
import pathlib
import threading

import pytest

pytestmark = pytest.mark.unit

_CW = pathlib.Path(__file__).resolve().parents[1] / "common" / "code_wrapper.py"
_spec = importlib.util.spec_from_file_location("code_wrapper_only", _CW)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
CodeWrapper = _mod.CodeWrapper

FT = "2021-03-14T15:09:26"


def _run(user_code: str, fake_datetime=FT, limit: float = 8.0) -> dict:
    """Exec the wrapped code on its own event loop in a daemon thread, guarded by a
    REAL-clock join timeout — so if the freeze ever re-breaks asyncio (monotonic
    frozen -> awaits never wake) the test fails fast instead of hanging forever."""
    wrapped = CodeWrapper.wrap_code(user_code, fake_datetime=fake_datetime)
    compile(wrapped, "<wrapped>", "exec")  # must be valid Python
    box: dict = {}

    def target():
        try:
            g: dict = {}
            exec(wrapped, g)
            box["res"] = asyncio.run(g["_async_main"]())
        except Exception as e:  # noqa: BLE001 — surfaced below
            box["err"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(limit)
    assert not t.is_alive(), (
        "wrapped async code did not finish — freeze is breaking asyncio (monotonic frozen)"
    )
    if "err" in box:
        raise box["err"]
    return box["res"]


def test_asyncio_sleep_survives_freeze():
    # The regression guard: an await inside frozen user code must still progress.
    res = _run(
        "import asyncio, time\n"
        "_m0 = time.monotonic()\n"
        "await asyncio.sleep(0.4)\n"
        "mono_advanced = time.monotonic() - _m0\n"
        "done = True\n"
    )
    assert res["done"] is True
    assert res["mono_advanced"] > 0.3  # monotonic NOT frozen — asyncio timers work


def test_freezes_datetime_date_and_time_module():
    res = _run(
        "import datetime, time\n"
        "now = datetime.datetime.now().isoformat()\n"
        "today = datetime.date.today().isoformat()\n"
        "epoch = int(time.time())\n"
        "utc = time.strftime('%Y-%m-%d', time.gmtime())\n"
    )
    assert res["now"].startswith("2021-03-14T15:09:26")
    assert res["today"] == "2021-03-14"  # datetime.date, missed by the old shim
    assert res["utc"] == "2021-03-14"  # time module, missed by the old shim
    # time.time() uses the UTC interpretation of the naive freeze time, like AppWorld.
    assert res["epoch"] == calendar.timegm(dtmod.datetime.fromisoformat(FT).timetuple())


def test_freeze_is_torn_down_no_host_leak():
    before = dtmod.date.today()
    _run("import datetime\nx = datetime.date.today().isoformat()\n")
    after = dtmod.date.today()
    assert before == after
    assert after.year >= 2026  # host clock unaffected by the sandbox freeze


def test_helper_vars_are_underscore_prefixed_for_filtering():
    # VariableUtils.filter_new_variables drops '_'-prefixed keys, so freeze
    # machinery in locals() must be underscore-prefixed.
    res = _run("y = 5\n")
    assert all(k.startswith("_") for k in res if k.startswith("_cuga"))
    assert res["y"] == 5


def test_no_fake_datetime_injects_no_freeze():
    wrapped = CodeWrapper.wrap_code("a = 1\na + 1", fake_datetime=None)
    compile(wrapped, "<w>", "exec")
    assert "_cuga" not in wrapped
    assert "_async_main" in wrapped


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"ok  {_name}")
    print("ALL OK")
