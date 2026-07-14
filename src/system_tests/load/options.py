"""Shared load-test CLI/env options for pytest."""

import os

DEFAULT_LOAD_TEST_USERS = 5
LOAD_TEST_USERS_ENV = "CUGA_LOAD_TEST_USERS"
LOAD_TEST_USERS_OPTION = "--load-test-users"


def add_load_test_users_option(parser):
    parser.addoption(
        LOAD_TEST_USERS_OPTION,
        action="store",
        default=None,
        type=int,
        help=(
            "Number of concurrent user simulations for load tests "
            f"(default: {DEFAULT_LOAD_TEST_USERS}, or ${LOAD_TEST_USERS_ENV})"
        ),
    )


def configure_load_test_users(config):
    option = config.getoption("load_test_users", default=None)
    if option is not None:
        os.environ[LOAD_TEST_USERS_ENV] = str(option)


def resolve_load_test_users(default: int = DEFAULT_LOAD_TEST_USERS) -> int:
    """Resolve concurrent user count from pytest CLI flag or env."""
    raw = os.environ.get(LOAD_TEST_USERS_ENV)
    if raw is None:
        return default
    users = int(raw)
    if users < 1:
        raise ValueError(f"{LOAD_TEST_USERS_ENV} must be >= 1, got {users}")
    return users
