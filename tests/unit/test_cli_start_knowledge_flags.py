"""`cuga start` knowledge flags reach settings that were loaded before the flags were set.

The config that `cuga start` seeds for the server is built from settings in the CLI process,
and the server applies that config at startup. A flag that only reached os.environ was lost.
"""

import os

import pytest

from cuga.backend.knowledge.config import KnowledgeConfig
from cuga.config import settings

pytestmark = pytest.mark.unit

_ENABLED = "DYNACONF_KNOWLEDGE__ENABLED"
_CHUNK = "DYNACONF_KNOWLEDGE__CHUNKING__CHUNK_SIZE"


@pytest.fixture
def clean_knowledge_env():
    saved = {k: os.environ.pop(k) for k in (_ENABLED, _CHUNK) if k in os.environ}
    settings.reload()
    settings.validators.validate()
    yield
    for k in (_ENABLED, _CHUNK):
        os.environ.pop(k, None)
    os.environ.update(saved)
    settings.reload()
    settings.validators.validate()


def test_knowledge_flags_reach_already_loaded_settings(clean_knowledge_env):
    from cuga.cli.main import _apply_knowledge_flag_env

    assert KnowledgeConfig.from_settings(settings).enabled is False

    _apply_knowledge_flag_env([(_ENABLED, "true"), (_CHUNK, "512"), ("DYNACONF_KNOWLEDGE__UNSET", None)])

    config = KnowledgeConfig.from_settings(settings)
    assert config.enabled is True
    assert config.chunk_size == 512
    assert "DYNACONF_KNOWLEDGE__UNSET" not in os.environ
