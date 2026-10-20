"""Checkpointer settings under ``[storage]`` (issue #774, W01.2).

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
    "checkpoint_retention_days": 90,
    "checkpoint_prune_interval_minutes": 60,
    "thread_lease_ttl_seconds": 60,
    "thread_lease_renew_seconds": 20,
}

INTEGER_KEYS = [k for k, v in EXPECTED_DEFAULTS.items() if isinstance(v, int)]


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


def test_every_key_has_a_validator_with_the_same_default():
    validators = {v.names[0]: v for v in _storage_validators()}
    assert set(validators) == {f"storage.{k}" for k in EXPECTED_DEFAULTS}
    for key, expected in EXPECTED_DEFAULTS.items():
        assert validators[f"storage.{key}"].default == expected, key


def test_validators_supply_defaults_when_the_keys_are_missing(tmp_path):
    isolated = _isolated_settings(tmp_path)
    isolated.validators.validate_all()
    for key, expected in EXPECTED_DEFAULTS.items():
        assert isolated.storage[key] == expected, key


@pytest.mark.parametrize("value", ["durable", "memory"])
def test_checkpointer_accepts_known_values(tmp_path, value):
    isolated = _isolated_settings(tmp_path, f'checkpointer = "{value}"')
    isolated.validators.validate_all()
    assert isolated.storage.checkpointer == value


def test_checkpointer_rejects_unknown_value(tmp_path):
    isolated = _isolated_settings(tmp_path, 'checkpointer = "foo"')
    with pytest.raises(ValidationError, match="storage.checkpointer"):
        isolated.validators.validate_all()


@pytest.mark.parametrize("key", INTEGER_KEYS)
def test_integer_settings_reject_negative_values(tmp_path, key):
    isolated = _isolated_settings(tmp_path, f"{key} = -1")
    with pytest.raises(ValidationError, match=f"storage.{key}"):
        isolated.validators.validate_all()


@pytest.mark.parametrize("key", INTEGER_KEYS)
def test_integer_settings_accept_zero(tmp_path, key):
    isolated = _isolated_settings(tmp_path, f"{key} = 0")
    isolated.validators.validate_all()
    assert isolated.storage[key] == 0
