from __future__ import annotations

import copy
import json
import os
import tempfile
from typing import Any

import pytest

from cuga.backend.cuga_graph.policy.folder_loader import (
    create_tool_guide_from_markdown,
    parse_markdown_with_frontmatter,
)
from cuga.backend.cuga_graph.policy.filesystem_sync import PolicyFilesystemSync
from cuga.backend.cuga_graph.policy.models import AlwaysTrigger, ToolGuard, ToolGuide
from cuga.backend.cuga_graph.policy.utils import (
    apply_policies_data_to_storage,
    export_policies_to_json,
)
from cuga.backend.server.main import _policy_to_frontend_dict

# ---------------------------------------------------------------------------
# Real policy data (Finance eligibility revenue requirements, guards pre-generated)
# ---------------------------------------------------------------------------

FINANCE_POLICY_EXPORT = {
    "enablePolicies": True,
    "policies": [
        {
            "id": "tool_guide_1781098827245",
            "name": "Finance eligibility revenue requirements",
            "description": "Accounts cannot be created for companies from the Finance industry with annual revenue under $100,000.",
            "policy_type": "tool_guide",
            "enabled": True,
            "triggers": [{"type": "always"}],
            "priority": 50,
            "target_tools": ["crm_create_account_accounts_post"],
            "target_apps": None,
            "guide_content": "## Accounts cannot be created for companies from the Finance industry with annual revenue under $100,000.\n",
            "tool_guards": {
                "crm_create_account_accounts_post": {
                    "violating_examples": [
                        'Calling crm_create_account_accounts_post with name="Acme Capital", industry="Finance", annual_revenue=99999.99 violates the policy because the company is in Finance and revenue is below $100,000.',
                        'Creating an account with name="Budget Bank", industry="Finance", annual_revenue=0 violates the policy because zero revenue is under the $100,000 minimum for Finance companies.',
                        'Creating an account with name="Startup Lending LLC", industry="Finance", annual_revenue=50000 violates the policy even if all other fields such as website, phone, and address are valid, because the Finance revenue threshold is not met.',
                        'Calling crm_create_account_accounts_post with name="Micro Investments", industry="Finance", annual_revenue=99999 violates the policy because the revenue is one dollar below the allowed boundary.',
                        'Creating an account with name="Negative Revenue Finance Co", industry="Finance", annual_revenue=-1000 violates the policy because negative annual revenue is under $100,000.',
                        'Creating an account with name="Lowercase Finance Firm", industry="finance", annual_revenue=75000 violates the policy because the industry is Finance despite casing differences and the revenue is below $100,000.',
                    ],
                    "compliance_examples": [
                        'Calling crm_create_account_accounts_post with name="Prime Capital", industry="Finance", annual_revenue=100000 complies with the policy because Finance companies are allowed when annual revenue is exactly $100,000.',
                        'Creating an account with name="Global Finance Partners", industry="Finance", annual_revenue=250000 complies because the company is in Finance but its annual revenue is above the $100,000 threshold.',
                        'Creating an account with name="Small Retail Shop", industry="Retail", annual_revenue=50000 complies because the revenue restriction applies only to Finance companies.',
                        'Calling crm_create_account_accounts_post with name="Healthcare Startup", industry="Healthcare", annual_revenue=0 complies because non-Finance industries are not restricted by this policy.',
                        'Creating an account with name="No Industry Account", annual_revenue=75000 and no industry value complies because the account is not identified as being in the Finance industry.',
                        'Creating an account with name="Finance Firm No Revenue Provided", industry="Finance" and omitting annual_revenue complies with this policy because the policy only prohibits Finance accounts when annual revenue is explicitly under $100,000.',
                    ],
                    "policy_code": (
                        "from typing import *\n\n"
                        "from toolguard.runtime import PolicyViolationException, assert_any_condition_met, rule\n"
                        "from crm.crm_types import *\n"
                        "from crm.i_crm import ICrm\n\n"
                        '@rule("Finance eligibility revenue requirements")\n'
                        "async def guard_finance_eligibility_revenue_requirements(api: ICrm, args: CrmCreateAccountAccountsPostArgs):\n"
                        "    industry = args.industry\n"
                        "    annual_revenue = args.annual_revenue\n\n"
                        "    if (\n"
                        "        isinstance(industry, str)\n"
                        '        and industry.strip().lower() == "finance"\n'
                        "        and annual_revenue is not None\n"
                        "        and annual_revenue < 100000\n"
                        "    ):\n"
                        "        raise PolicyViolationException(\n"
                        '            "Accounts cannot be created for Finance companies with annual revenue under $100,000."\n'
                        "        )"
                    ),
                }
            },
            "prepend": False,
            "guards_enabled": True,
        }
    ],
}

FINANCE_POLICY_DICT = FINANCE_POLICY_EXPORT["policies"][0]
TOOL_NAME = "crm_create_account_accounts_post"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class FakePolicyStorage:
    def __init__(self) -> None:
        self.policies: list[Any] = []

    async def list_policies(self, enabled_only: bool = False, **kwargs: Any) -> list[Any]:
        return list(self.policies)

    async def delete_policy(self, policy_id: str) -> None:
        self.policies = [p for p in self.policies if p.id != policy_id]

    async def add_policy(self, policy: Any) -> None:
        self.policies.append(policy)


def _finance_policy_dict_with_guards_enabled(guards_enabled: bool) -> dict[str, Any]:
    policy_dict = copy.deepcopy(FINANCE_POLICY_DICT)
    policy_dict["guards_enabled"] = guards_enabled
    return policy_dict


def _assert_finance_guards_intact(policy: ToolGuide, *, guards_enabled: bool) -> None:
    """Common assertions that the Finance policy's guards survived a round-trip."""
    assert isinstance(policy, ToolGuide)
    assert policy.id == "tool_guide_1781098827245"
    assert policy.guards_enabled is guards_enabled
    guard = policy.tool_guards[TOOL_NAME]
    assert "Acme Capital" in guard.violating_examples[0]
    assert "Prime Capital" in guard.compliance_examples[0]
    assert "PolicyViolationException" in guard.policy_code


# _policy_to_frontend_dict expects model_dump() output which uses "type" not "policy_type"
FINANCE_POLICY_MODEL_DUMP = {**FINANCE_POLICY_DICT, "type": FINANCE_POLICY_DICT["policy_type"]}

# ---------------------------------------------------------------------------
# Test 1: frontend export → import round-trip (HTTP / UI path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_frontend_export_import_roundtrip_preserves_tool_guards_and_guards_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_calls: list[str] = []

    async def fail_if_generation_is_called(*args: Any, **kwargs: Any) -> None:
        generation_calls.append("called")
        raise AssertionError("ToolGuard generation must not run during import")

    monkeypatch.setattr(
        "cuga.backend.server.tool_guard_generation.generate_tool_guards_for_policy",
        fail_if_generation_is_called,
    )

    exported = _policy_to_frontend_dict({**FINANCE_POLICY_MODEL_DUMP, "guards_enabled": False})
    storage = FakePolicyStorage()

    result = await apply_policies_data_to_storage(
        storage, [exported], clear_existing=True, filesystem_sync=None
    )

    assert result == {"count": 1, "errors": []}
    assert generation_calls == [], "generation must not be invoked during import"
    assert len(storage.policies) == 1
    _assert_finance_guards_intact(storage.policies[0], guards_enabled=False)


# ---------------------------------------------------------------------------
# Test 2: SDK export (export_policies_to_json) → import round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdk_export_import_roundtrip_preserves_tool_guards_and_guards_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_calls: list[str] = []

    async def fail_if_generation_is_called(*args: Any, **kwargs: Any) -> None:
        generation_calls.append("called")
        raise AssertionError("ToolGuard generation must not run during import")

    monkeypatch.setattr(
        "cuga.backend.server.tool_guard_generation.generate_tool_guards_for_policy",
        fail_if_generation_is_called,
    )

    # Seed source storage with the real Finance policy.
    # FINANCE_POLICY_DICT intentionally uses "policy_type" (the frontend/UI key), while
    # the round-trip export uses "type" (model_dump key).  Both are handled by
    # apply_policies_data_to_storage (utils.py: policy_data.get("policy_type") or ...get("type")),
    # so this seed exercises the "policy_type" fallback path and the re-import exercises the "type" path.
    source = FakePolicyStorage()
    await apply_policies_data_to_storage(
        source,
        [_finance_policy_dict_with_guards_enabled(False)],
        clear_existing=False,
        filesystem_sync=None,
    )

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        ok = await export_policies_to_json(source, tmp_path)
        assert ok

        with open(tmp_path) as f:
            exported_data = json.load(f)

        assert len(exported_data) == 1
        # SDK export uses model_dump — guards_enabled must be present and preserved
        assert exported_data[0]["guards_enabled"] is False
        assert TOOL_NAME in exported_data[0]["tool_guards"]

        dest = FakePolicyStorage()
        result = await apply_policies_data_to_storage(
            dest, exported_data, clear_existing=True, filesystem_sync=None
        )

        assert result == {"count": 1, "errors": []}
        assert generation_calls == [], "generation must not be invoked during import"
        assert len(dest.policies) == 1
        _assert_finance_guards_intact(dest.policies[0], guards_enabled=False)
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Test 3: Markdown filesystem round-trip
#   _policy_to_markdown (write) → parse_markdown_with_frontmatter + create_tool_guide_from_markdown (read)
# ---------------------------------------------------------------------------


def test_markdown_filesystem_roundtrip_preserves_tool_guards_and_guards_enabled() -> None:
    # Build a ToolGuide directly (guards_enabled=False to prove it's preserved).
    # No async needed — all functions under test are synchronous.
    guard_data = FINANCE_POLICY_DICT["tool_guards"][TOOL_NAME]
    original = ToolGuide(
        id=FINANCE_POLICY_DICT["id"],
        name=FINANCE_POLICY_DICT["name"],
        description=FINANCE_POLICY_DICT["description"],
        triggers=[AlwaysTrigger()],
        target_tools=FINANCE_POLICY_DICT["target_tools"],
        guide_content=FINANCE_POLICY_DICT["guide_content"],
        tool_guards={TOOL_NAME: ToolGuard(**guard_data)},
        guards_enabled=False,
    )
    assert original.guards_enabled is False

    with tempfile.TemporaryDirectory() as tmpdir:
        sync = PolicyFilesystemSync(cuga_folder=tmpdir)
        sync._ensure_folder_structure()

        # Write to .md file
        md_path = sync.save_policy_to_file(original)
        assert os.path.exists(md_path)

        # Read it back via folder_loader
        frontmatter, md_content = parse_markdown_with_frontmatter(md_path)

        assert frontmatter.get("guards_enabled") is False, (
            "_policy_to_markdown must write guards_enabled into frontmatter"
        )
        assert TOOL_NAME in (frontmatter.get("tool_guards") or {}), (
            "_policy_to_markdown must write tool_guards into frontmatter"
        )

        loaded: ToolGuide = create_tool_guide_from_markdown(md_path, frontmatter, md_content)

    _assert_finance_guards_intact(loaded, guards_enabled=False)


def test_markdown_filesystem_roundtrip_handles_dashes_inside_tool_guard_code() -> None:
    guard_data = copy.deepcopy(FINANCE_POLICY_DICT["tool_guards"][TOOL_NAME])
    guard_data["policy_code"] = (
        "def guard_tool_call(context):\n    # --- section divider generated by an LLM ---\n    return True\n"
    )
    original = ToolGuide(
        id=FINANCE_POLICY_DICT["id"],
        name=FINANCE_POLICY_DICT["name"],
        description=FINANCE_POLICY_DICT["description"],
        triggers=[AlwaysTrigger()],
        target_tools=FINANCE_POLICY_DICT["target_tools"],
        guide_content=FINANCE_POLICY_DICT["guide_content"],
        tool_guards={TOOL_NAME: ToolGuard(**guard_data)},
        guards_enabled=True,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        sync = PolicyFilesystemSync(cuga_folder=tmpdir)
        sync._ensure_folder_structure()
        md_path = sync.save_policy_to_file(original)

        frontmatter, md_content = parse_markdown_with_frontmatter(md_path)
        loaded: ToolGuide = create_tool_guide_from_markdown(md_path, frontmatter, md_content)

    loaded_guard = loaded.tool_guards[TOOL_NAME]
    assert "# --- section divider generated by an LLM ---" in loaded_guard.policy_code
    assert loaded.guards_enabled is True


def test_frontmatter_parser_ignores_indented_dashes_inside_yaml_block() -> None:
    markdown = (
        "---\n"
        "id: policy_with_block\n"
        "name: Policy With Block\n"
        "description: Block scalar includes indented dashes\n"
        "type: tool_guide\n"
        "priority: 50\n"
        "enabled: true\n"
        "target_tools:\n"
        "  - crm_create_account_accounts_post\n"
        "tool_guards:\n"
        "  crm_create_account_accounts_post:\n"
        "    violating_examples: []\n"
        "    compliance_examples: []\n"
        "    policy_code: |\n"
        "      # --- section divider inside YAML block ---\n"
        "      def guard_tool_call(context):\n"
        "          return True\n"
        "---\n"
        "## guide content\n"
    )

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as tmp:
        tmp.write(markdown)
        tmp_path = tmp.name

    try:
        frontmatter, md_content = parse_markdown_with_frontmatter(tmp_path)
    finally:
        os.unlink(tmp_path)

    guard = frontmatter["tool_guards"][TOOL_NAME]
    assert "# --- section divider inside YAML block ---" in guard["policy_code"]
    assert md_content == "## guide content"


@pytest.mark.asyncio
async def test_json_import_skips_invalid_guard_entries_and_preserves_valid_ones() -> None:
    policy_dict = copy.deepcopy(FINANCE_POLICY_DICT)
    policy_dict["tool_guards"] = {
        TOOL_NAME: policy_dict["tool_guards"][TOOL_NAME],
        "broken_guard": {
            "violating_examples": "not a list",
            "compliance_examples": [],
            "policy_code": "def broken(): pass",
        },
    }
    storage = FakePolicyStorage()

    result = await apply_policies_data_to_storage(
        storage, [policy_dict], clear_existing=True, filesystem_sync=None
    )

    assert result["count"] == 1
    assert len(result["errors"]) == 1
    assert "invalid guard for tool 'broken_guard'" in result["errors"][0]
    assert len(storage.policies) == 1
    loaded = storage.policies[0]
    assert TOOL_NAME in loaded.tool_guards
    assert "broken_guard" not in loaded.tool_guards
    _assert_finance_guards_intact(loaded, guards_enabled=True)


@pytest.mark.asyncio
async def test_json_import_treats_null_guards_enabled_as_true() -> None:
    policy_dict = copy.deepcopy(FINANCE_POLICY_DICT)
    policy_dict["guards_enabled"] = None
    storage = FakePolicyStorage()

    result = await apply_policies_data_to_storage(
        storage, [policy_dict], clear_existing=True, filesystem_sync=None
    )

    assert result == {"count": 1, "errors": []}
    assert storage.policies[0].guards_enabled is True
