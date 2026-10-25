"""Checkpointer settings under ``[storage]`` (issue #774).

Rejection tests build a private ``Dynaconf`` instance from the real
``cuga.config.validators`` so the global ``settings`` object is never mutated.
"""

import pytest
from dynaconf import Dynaconf, ValidationError

pytestmark = pytest.mark.unit

EXPECTED_DEFAULTS = {
    "checkpointer": "memory",
    "checkpoint_db_path": "",
    "checkpoint_pool_min_size": 1,
    "checkpoint_pool_max_size": 10,
    "thread_lease_ttl_seconds": 60,
    "thread_lease_renew_seconds": 20,
}


def _storage_validators():
    from cuga.config import validators

    names = {f"storage.{key}" for key in EXPECTED_DEFAULTS}
    return [v for v in validators if v.names and v.names[0] in names]


def _isolated_settings(tmp_path, storage_toml: str = "") -> Dynaconf:
    """A Dynaconf built only from a temp file, with an env prefix nothing sets."""
    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(f"[storage]\n{storage_toml}\n")
    return Dynaconf(
        settings_files=[str(settings_file)],
        envvar_prefix="CUGA_TEST_CHECKPOINT_SETTINGS",
        validators=_storage_validators(),
    )


def test_settings_toml_ships_the_defaults():
    from cuga.config import settings

    for key, expected in EXPECTED_DEFAULTS.items():
        assert settings.storage[key] == expected, key


def test_checkpointer_rejects_unknown_value(tmp_path):
    isolated = _isolated_settings(tmp_path, 'checkpointer = "foo"')
    with pytest.raises(ValidationError, match="storage.checkpointer"):
        isolated.validators.validate_all()


def test_pool_sizes_must_be_at_least_one(tmp_path):
    isolated = _isolated_settings(tmp_path, "checkpoint_pool_max_size = 0")
    with pytest.raises(ValidationError, match="storage.checkpoint_pool_max_size"):
        isolated.validators.validate_all()
