"""Manage endpoints: draft config (auto-save) and publish (new version)."""

from loguru import logger

from cuga.backend.server.manage_routes.apply import (
    apply_llm_to_draft_state,
    apply_llm_to_state,
    apply_published_config,
)
from cuga.backend.server.manage_routes.helpers import (
    AGENT_DRAFT_LOCKS,
    extract_agent_feature_overrides,
    is_secret_field_name,
    load_and_patch_draft,
    merge_feature_flags_defaults,
    merge_mcp_yaml_into_config,
    redact_secrets_in_config,
    save_draft_section_unlocked,
)
from cuga.backend.server.manage_routes.knowledge_reindex import (
    deferred_reindex_complete_and_flip,
    migrate_and_reindex_for_agent,
    persist_active_vector_config,
)
from cuga.backend.server.manage_routes.router import router

from cuga.backend.server.manage_routes import (  # noqa: F401
    config_routes,
    draft_routes,
    knowledge_routes,
    llm_routes,
)

# Backward-compatible aliases
_extract_agent_feature_overrides = extract_agent_feature_overrides
_merge_feature_flags_defaults = merge_feature_flags_defaults
_merge_mcp_yaml_into_config = merge_mcp_yaml_into_config
_redact_secrets_in_config = redact_secrets_in_config
_is_secret_field_name = is_secret_field_name
_load_and_patch_draft = load_and_patch_draft
_save_draft_section_unlocked = save_draft_section_unlocked
_apply_published_config = apply_published_config
_apply_llm_to_state = apply_llm_to_state
_apply_llm_to_draft_state = apply_llm_to_draft_state
_AGENT_DRAFT_LOCKS = AGENT_DRAFT_LOCKS
_deferred_reindex_complete_and_flip = deferred_reindex_complete_and_flip
_migrate_and_reindex_for_agent = migrate_and_reindex_for_agent
_persist_active_vector_config = persist_active_vector_config

from cuga.backend.server.manage_routes.knowledge_routes import patch_draft_knowledge  # noqa: E402

__all__ = [
    "router",
    "logger",
    "patch_draft_knowledge",
    "_extract_agent_feature_overrides",
    "_merge_feature_flags_defaults",
    "_merge_mcp_yaml_into_config",
    "_redact_secrets_in_config",
    "_is_secret_field_name",
    "_load_and_patch_draft",
    "_save_draft_section_unlocked",
    "_apply_published_config",
    "_apply_llm_to_state",
    "_apply_llm_to_draft_state",
    "_AGENT_DRAFT_LOCKS",
    "_deferred_reindex_complete_and_flip",
    "_migrate_and_reindex_for_agent",
    "_persist_active_vector_config",
]
