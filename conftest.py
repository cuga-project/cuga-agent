"""Root pytest configuration: workspace seeding, load options, stability threshold."""

from __future__ import annotations

import shutil
from pathlib import Path

_stability_outcomes: dict[str, bool] = {}
_hard_failure = False


def pytest_addoption(parser):
    from system_tests.load.options import add_load_test_users_option

    add_load_test_users_option(parser)
    parser.addoption(
        "--stability-threshold",
        action="store",
        default=None,
        type=float,
        help="Minimum stability test pass rate (percent) required to exit 0 (default: disabled)",
    )


def pytest_configure(config):
    from system_tests.load.options import configure_load_test_users

    configure_load_test_users(config)


def _seed_cuga_workspace() -> None:
    workspace = Path("cuga_workspace")
    source = Path("src/cuga/demo_tools/huggingface")
    if not source.is_dir():
        return
    workspace.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        dest = workspace / item.name
        if item.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def pytest_sessionstart(session):
    global _stability_outcomes, _hard_failure
    _stability_outcomes = {}
    _hard_failure = False

    # xdist workers each have workerinput; seed once on the controller only.
    if getattr(session.config, "workerinput", None) is not None:
        return
    _seed_cuga_workspace()


def pytest_runtest_logreport(report):
    """Track one stability outcome per nodeid.

    Prefer ``call`` when present. Count setup failure only when the test never
    reached call. Teardown failures do not rewrite the rate (avoids
    double-counting) and are treated as hard failures so the threshold cannot
    mask cleanup errors.
    """
    global _hard_failure
    keywords = getattr(report, "keywords", {})
    is_stability = "stability" in keywords

    if report.when == "call":
        if is_stability:
            _stability_outcomes[report.nodeid] = report.passed
        elif report.failed:
            _hard_failure = True
        return

    if not report.failed:
        return

    if is_stability:
        if report.when == "setup" and report.nodeid not in _stability_outcomes:
            _stability_outcomes[report.nodeid] = False
        elif report.when == "teardown":
            _hard_failure = True
        return

    _hard_failure = True


def pytest_sessionfinish(session, exitstatus):
    threshold = session.config.getoption("stability_threshold")
    if threshold is None or not _stability_outcomes:
        return

    passed = sum(_stability_outcomes.values())
    total = len(_stability_outcomes)
    pass_rate = 100.0 * passed / total
    print(f"\nStability pass rate: {pass_rate:.1f}% ({passed}/{total}), threshold: {threshold}%")

    if _hard_failure:
        return

    session.exitstatus = 0 if pass_rate >= threshold else 1
