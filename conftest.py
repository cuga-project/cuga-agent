"""Root pytest configuration: workspace seeding, load options, stability threshold."""

from __future__ import annotations

import shutil
from pathlib import Path

from system_tests.load.options import (
    add_load_test_users_option,
    configure_load_test_users,
)

_stability_outcomes: list[bool] = []
_non_stability_failure = False


def pytest_addoption(parser):
    add_load_test_users_option(parser)
    parser.addoption(
        "--stability-threshold",
        action="store",
        default=None,
        type=float,
        help="Minimum stability test pass rate (percent) required to exit 0 (default: disabled)",
    )


def pytest_configure(config):
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
    global _stability_outcomes, _non_stability_failure
    _stability_outcomes = []
    _non_stability_failure = False

    # xdist workers each have workerinput; seed once on the controller only.
    if getattr(session.config, "workerinput", None) is not None:
        return
    _seed_cuga_workspace()


def pytest_runtest_logreport(report):
    global _non_stability_failure
    keywords = getattr(report, "keywords", {})
    if report.when == "call":
        if "stability" in keywords:
            _stability_outcomes.append(report.passed)
        elif report.failed:
            _non_stability_failure = True
    elif report.failed:
        if "stability" in keywords:
            _stability_outcomes.append(False)
        else:
            _non_stability_failure = True


def pytest_sessionfinish(session, exitstatus):
    threshold = session.config.getoption("stability_threshold")
    if threshold is None or not _stability_outcomes:
        return

    passed = sum(_stability_outcomes)
    total = len(_stability_outcomes)
    pass_rate = 100.0 * passed / total
    print(f"\nStability pass rate: {pass_rate:.1f}% ({passed}/{total}), threshold: {threshold}%")

    if _non_stability_failure:
        return

    session.exitstatus = 0 if pass_rate >= threshold else 1
