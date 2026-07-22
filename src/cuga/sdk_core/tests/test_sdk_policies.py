"""
Integration tests for CUGA SDK Policy System

These tests validate the policy management functionality including:
- Tool Approval policies
- Playbook policies
- Intent Guard policies
- Tool Guide policies
"""

import pytest
import pytest_asyncio
from langchain_core.tools import tool

from cuga import CugaAgent


@pytest_asyncio.fixture(autouse=True, scope="function")
async def clean_policy_storage():
    """Clean up policy storage before each test to ensure isolation."""
    agent = CugaAgent(tools=[])

    # Get all policies and delete them
    policies = await agent.policies.list()
    for policy in policies:
        await agent.policies.delete(policy["id"])

    yield

    # Clean up after test as well
    policies = await agent.policies.list()
    for policy in policies:
        await agent.policies.delete(policy["id"])


@tool
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email to a recipient"""
    return f"Email sent to {to} with subject '{subject}'"


@tool
def delete_record(record_id: str) -> str:
    """Delete a record from the database"""
    return f"Deleted record {record_id}"


@tool
def read_data(query: str) -> str:
    """Read data from the database"""
    return f"Data for query: {query}"


@tool
def calculate_sum(a: int, b: int) -> int:
    """Calculate the sum of two numbers"""
    return a + b


class TestSDKToolApprovalPolicy:
    """Integration tests for Tool Approval policies"""

    @pytest.mark.asyncio
    async def test_tool_approval_policy_basic(self):
        """Test basic tool approval policy functionality"""
        agent = CugaAgent(tools=[delete_record])

        policy_id = await agent.policies.add_tool_approval(
            name="Approve Delete Operations",
            required_tools=["delete_record"],
            approval_message="This will delete data. Please confirm.",
        )

        assert policy_id is not None
        assert policy_id.startswith("tool_approval_")

        policies = await agent.policies.list()
        assert len(policies) == 1
        assert policies[0]["name"] == "Approve Delete Operations"
        assert policies[0]["type"] == "tool_approval"

    @pytest.mark.asyncio
    async def test_tool_approval_multiple_tools(self):
        """Test tool approval policy with multiple tools"""
        agent = CugaAgent(tools=[delete_record, read_data])

        policy_id = await agent.policies.add_tool_approval(
            name="Approve Deletions",
            required_tools=["delete_record"],
            approval_message="Deletion requires approval.",
        )

        assert policy_id is not None

        # Verify policy was created
        policies = await agent.policies.list()
        assert len(policies) == 1
        assert policies[0]["name"] == "Approve Deletions"

        # Verify we can get the policy details
        policy = await agent.policies.get(policy_id)
        assert policy is not None
        assert policy["policy"].required_tools == ["delete_record"]
        assert policy["policy"].approval_message == "Deletion requires approval."

    @pytest.mark.asyncio
    async def test_tool_approval_wildcard(self):
        """Test tool approval with wildcard to require approval for all tools"""
        agent = CugaAgent(tools=[send_email, delete_record])

        policy_id = await agent.policies.add_tool_approval(
            name="Approve All Tools",
            required_tools=["*"],
            approval_message="All tool usage requires approval.",
        )

        assert policy_id is not None

        policies = await agent.policies.list()
        assert len(policies) == 1

        policy_details = await agent.policies.get(policy_id)
        assert policy_details is not None
        assert policy_details["policy"].required_tools == ["*"]

    @pytest.mark.asyncio
    async def test_tool_approval_delete_policy(self):
        """Test deleting a tool approval policy"""
        agent = CugaAgent(tools=[delete_record])

        policy_id = await agent.policies.add_tool_approval(
            name="Temporary Approval",
            required_tools=["delete_record"],
        )

        policies = await agent.policies.list()
        assert len(policies) == 1

        success = await agent.policies.delete(policy_id)
        assert success is True

        policies = await agent.policies.list()
        assert len(policies) == 0


class TestSDKPlaybookPolicy:
    """Integration tests for Playbook policies"""

    @pytest.mark.asyncio
    async def test_playbook_policy_with_keywords(self):
        """Test creating a playbook policy with keyword triggers"""
        agent = CugaAgent(tools=[send_email])

        playbook_content = """
# Customer Onboarding Process

## Steps
1. Verify customer email
2. Send welcome email
3. Create customer account
4. Assign customer ID

## Important Notes
- Always verify email format
- Use standard welcome template
"""

        policy_id = await agent.policies.add_playbook(
            name="Customer Onboarding",
            content=playbook_content,
            keywords=["onboard", "signup", "register", "new customer"],
            description="Guide for onboarding new customers",
        )

        assert policy_id is not None
        assert policy_id.startswith("playbook_")

        policies = await agent.policies.list()
        assert len(policies) == 1
        assert policies[0]["name"] == "Customer Onboarding"
        assert policies[0]["type"] == "playbook"

    @pytest.mark.asyncio
    async def test_playbook_policy_with_natural_language(self):
        """Test creating a playbook policy with natural language trigger"""
        agent = CugaAgent(tools=[calculate_sum])

        playbook_content = """
# Data Processing Guidelines

## Security Requirements
- Always validate input data
- Log all operations
- Use encryption for sensitive data
"""

        policy_id = await agent.policies.add_playbook(
            name="Data Processing",
            content=playbook_content,
            natural_language_trigger=["processing sensitive customer data"],
            threshold=0.7,
        )

        assert policy_id is not None

        policy_details = await agent.policies.get(policy_id)
        assert policy_details is not None
        assert "Data Processing" in policy_details["name"]

    @pytest.mark.asyncio
    async def test_playbook_policy_priority(self):
        """Test playbook policies with different priorities"""
        agent = CugaAgent(tools=[send_email])

        policy_id_high = await agent.policies.add_playbook(
            name="High Priority Playbook",
            content="# High priority content",
            keywords=["urgent"],
            priority=100,
        )

        policy_id_low = await agent.policies.add_playbook(
            name="Low Priority Playbook",
            content="# Low priority content",
            keywords=["normal"],
            priority=10,
        )

        policies = await agent.policies.list()
        assert len(policies) == 2

        high_policy = await agent.policies.get(policy_id_high)
        low_policy = await agent.policies.get(policy_id_low)

        assert high_policy["priority"] == 100
        assert low_policy["priority"] == 10

    @pytest.mark.asyncio
    async def test_playbook_policy_list_and_get(self):
        """Test listing and getting playbook policies"""
        agent = CugaAgent(tools=[send_email])

        policy_id = await agent.policies.add_playbook(
            name="Test Playbook",
            content="# Test content",
            keywords=["test"],
        )

        policies = await agent.policies.list()
        assert len(policies) == 1

        policy = await agent.policies.get(policy_id)
        assert policy is not None
        assert policy["id"] == policy_id
        assert policy["name"] == "Test Playbook"
        assert policy["type"] == "playbook"
        assert policy["policy"].markdown_content == "# Test content"


class TestSDKIntentGuardPolicy:
    """Integration tests for Intent Guard policies"""

    @pytest.mark.asyncio
    async def test_intent_guard_with_keywords(self):
        """Test creating an intent guard with keyword triggers"""
        agent = CugaAgent(tools=[delete_record])

        policy_id = await agent.policies.add_intent_guard(
            name="Block Delete Operations",
            keywords=["delete", "remove", "erase", "drop"],
            response="Deletion operations are not permitted in this system.",
            description="Prevents any deletion operations",
        )

        assert policy_id is not None
        assert policy_id.startswith("intent_guard_")

        policies = await agent.policies.list()
        assert len(policies) == 1
        assert policies[0]["name"] == "Block Delete Operations"
        assert policies[0]["type"] == "intent_guard"

    @pytest.mark.asyncio
    async def test_intent_guard_with_examples(self):
        """Test creating an intent guard with intent examples"""
        agent = CugaAgent(tools=[send_email])

        policy_id = await agent.policies.add_intent_guard(
            name="Block Spam",
            keywords=["spam", "bulk email", "mass mail"],
            intent_examples=[
                "Send emails to all users",
                "Blast email to everyone",
                "Mass email campaign",
            ],
            response="Bulk email operations are not allowed.",
        )

        assert policy_id is not None

        policy_details = await agent.policies.get(policy_id)
        assert policy_details is not None

        # Verify NL trigger was added
        nl_triggers = [t for t in policy_details["policy"].triggers if t.type == "natural_language"]
        assert len(nl_triggers) == 1
        assert len(nl_triggers[0].value) == 3

    @pytest.mark.asyncio
    async def test_intent_guard_priority_and_override(self):
        """Test intent guard with priority and override settings"""
        agent = CugaAgent(tools=[delete_record])

        policy_id = await agent.policies.add_intent_guard(
            name="Critical Security Guard",
            keywords=["admin", "root", "superuser"],
            response="Administrative operations are blocked.",
            priority=100,
            allow_override=False,
        )

        assert policy_id is not None

        policy_details = await agent.policies.get(policy_id)
        assert policy_details is not None
        assert policy_details["priority"] == 100
        assert policy_details["policy"].allow_override is False

    @pytest.mark.asyncio
    async def test_intent_guard_enabled_disabled(self):
        """Test enabling and disabling intent guards"""
        agent = CugaAgent(tools=[delete_record])

        policy_id_enabled = await agent.policies.add_intent_guard(
            name="Enabled Guard",
            keywords=["test"],
            response="Blocked",
            enabled=True,
        )

        policy_id_disabled = await agent.policies.add_intent_guard(
            name="Disabled Guard",
            keywords=["test2"],
            response="Blocked",
            enabled=False,
        )

        enabled_policy = await agent.policies.get(policy_id_enabled)
        disabled_policy = await agent.policies.get(policy_id_disabled)

        assert enabled_policy["enabled"] is True
        assert disabled_policy["enabled"] is False

    @pytest.mark.asyncio
    async def test_intent_guard_custom_response_types(self):
        """Test intent guard with different response types"""
        agent = CugaAgent(tools=[delete_record])

        policy_id_nl = await agent.policies.add_intent_guard(
            name="Natural Language Response",
            keywords=["delete"],
            response="This operation is not allowed.",
            response_type="natural_language",
        )

        policy_id_json = await agent.policies.add_intent_guard(
            name="JSON Response",
            keywords=["remove"],
            response='{"error": "Operation blocked", "code": 403}',
            response_type="json",
        )

        nl_policy = await agent.policies.get(policy_id_nl)
        json_policy = await agent.policies.get(policy_id_json)

        assert nl_policy["policy"].response.response_type == "natural_language"
        assert json_policy["policy"].response.response_type == "json"


class TestSDKToolGuidePolicy:
    """Integration tests for Tool Guide policies"""

    @pytest.mark.asyncio
    async def test_tool_guide_basic(self):
        """Test basic tool guide policy"""
        agent = CugaAgent(tools=[send_email])

        guide_content = """
## Security Guidelines
- Always verify recipient email
- Log all email operations
- Use approved templates only
"""

        policy_id = await agent.policies.add_tool_guide(
            name="Email Security Guidelines",
            content=guide_content,
            target_tools=["send_email"],
            description="Security guidelines for email operations",
        )

        assert policy_id is not None
        assert policy_id.startswith("tool_guide_")

        policies = await agent.policies.list()
        assert len(policies) == 1
        assert policies[0]["name"] == "Email Security Guidelines"
        assert policies[0]["type"] == "tool_guide"

    @pytest.mark.asyncio
    async def test_tool_guide_wildcard(self):
        """Test tool guide with wildcard to enrich all tools"""
        agent = CugaAgent(tools=[send_email, delete_record, read_data])

        guide_content = """
## General Guidelines
- Log all operations
- Handle errors gracefully
- Validate all inputs
"""

        policy_id = await agent.policies.add_tool_guide(
            name="Global Tool Guidelines",
            content=guide_content,
            target_tools=["*"],
        )

        assert policy_id is not None

        policy_details = await agent.policies.get(policy_id)
        assert policy_details is not None
        assert policy_details["policy"].target_tools == ["*"]

    @pytest.mark.asyncio
    async def test_tool_guide_prepend_vs_append(self):
        """Test tool guide with prepend and append options"""
        agent = CugaAgent(tools=[send_email])

        policy_id_prepend = await agent.policies.add_tool_guide(
            name="Prepend Guidelines",
            content="## IMPORTANT: Read this first",
            target_tools=["send_email"],
            prepend=True,
        )

        policy_id_append = await agent.policies.add_tool_guide(
            name="Append Guidelines",
            content="## Additional Notes",
            target_tools=["send_email"],
            prepend=False,
        )

        prepend_policy = await agent.policies.get(policy_id_prepend)
        append_policy = await agent.policies.get(policy_id_append)

        assert prepend_policy["policy"].prepend is True
        assert append_policy["policy"].prepend is False

    @pytest.mark.asyncio
    async def test_tool_guide_with_keywords(self):
        """Test tool guide with keyword triggers"""
        agent = CugaAgent(tools=[delete_record])

        guide_content = """
## Deletion Safety Guidelines
- Always backup before deletion
- Verify record ID
- Log deletion operations
"""

        policy_id = await agent.policies.add_tool_guide(
            name="Deletion Safety",
            content=guide_content,
            target_tools=["delete_record"],
            keywords=["delete", "remove"],
        )

        assert policy_id is not None

        policy_details = await agent.policies.get(policy_id)
        assert policy_details is not None
        assert len(policy_details["policy"].triggers) > 0

    @pytest.mark.asyncio
    async def test_tool_guide_priority(self):
        """Test tool guide with different priorities"""
        agent = CugaAgent(tools=[send_email])

        policy_id_high = await agent.policies.add_tool_guide(
            name="High Priority Guide",
            content="# Critical guidelines",
            target_tools=["send_email"],
            priority=100,
        )

        policy_id_low = await agent.policies.add_tool_guide(
            name="Low Priority Guide",
            content="# Optional guidelines",
            target_tools=["send_email"],
            priority=10,
        )

        high_policy = await agent.policies.get(policy_id_high)
        low_policy = await agent.policies.get(policy_id_low)

        assert high_policy["priority"] == 100
        assert low_policy["priority"] == 10


class TestSDKOutputFormatterPolicy:
    """Integration tests for OutputFormatter policies"""

    @pytest.mark.asyncio
    async def test_output_formatter_basic(self):
        """Test basic output formatter policy"""
        agent = CugaAgent(tools=[calculate_sum])

        format_config = """
Format the response as a structured summary with:
- A clear title using # Heading
- Key points as bullet points using - 
- Important information in **bold**
- A conclusion section

Make it professional and easy to read.
"""

        policy_id = await agent.policies.add_output_formatter(
            name="Summary Formatter",
            format_config=format_config,
            format_type="markdown",
            keywords=["summary", "result", "output"],
            description="Formats responses that contain summary keywords",
        )

        assert policy_id is not None
        assert policy_id.startswith("output_formatter_")

        policies = await agent.policies.list()
        assert len(policies) == 1
        assert policies[0]["name"] == "Summary Formatter"
        assert policies[0]["type"] == "output_formatter"

    @pytest.mark.asyncio
    async def test_output_formatter_with_natural_language(self):
        """Test output formatter with natural language trigger"""
        agent = CugaAgent(tools=[read_data])

        format_config = '{"status": "success", "data": "{{response}}"}'

        policy_id = await agent.policies.add_output_formatter(
            name="JSON Formatter",
            format_config=format_config,
            format_type="json_schema",
            natural_language_trigger=["response contains data results", "format as json"],
            threshold=0.7,
        )

        assert policy_id is not None

        policy_details = await agent.policies.get(policy_id)
        assert policy_details is not None
        assert policy_details["policy"].format_type == "json_schema"
        assert len(policy_details["policy"].triggers) > 0

    @pytest.mark.asyncio
    async def test_output_formatter_direct_type(self):
        """Test output formatter with direct type (string replacement)"""
        agent = CugaAgent(tools=[calculate_sum])

        policy_id = await agent.policies.add_output_formatter(
            name="Direct Replacement",
            format_config="This is a direct replacement message.",
            format_type="direct",
            keywords=["error", "failed"],
        )

        assert policy_id is not None

        policy_details = await agent.policies.get(policy_id)
        assert policy_details is not None
        assert policy_details["policy"].format_type == "direct"
        assert policy_details["policy"].format_config == "This is a direct replacement message."

    @pytest.mark.asyncio
    async def test_output_formatter_priority(self):
        """Test output formatter with different priorities"""
        agent = CugaAgent(tools=[send_email])

        policy_id_high = await agent.policies.add_output_formatter(
            name="High Priority Formatter",
            format_config="# High priority formatting",
            format_type="markdown",
            keywords=["important"],
            priority=100,
        )

        policy_id_low = await agent.policies.add_output_formatter(
            name="Low Priority Formatter",
            format_config="# Low priority formatting",
            format_type="markdown",
            keywords=["normal"],
            priority=10,
        )

        high_policy = await agent.policies.get(policy_id_high)
        low_policy = await agent.policies.get(policy_id_low)

        assert high_policy["priority"] == 100
        assert low_policy["priority"] == 10

    @pytest.mark.asyncio
    async def test_output_formatter_enabled_disabled(self):
        """Test enabling and disabling output formatters"""
        agent = CugaAgent(tools=[calculate_sum])

        policy_id_enabled = await agent.policies.add_output_formatter(
            name="Enabled Formatter",
            format_config="# Formatting",
            format_type="markdown",
            keywords=["test"],
            enabled=True,
        )

        policy_id_disabled = await agent.policies.add_output_formatter(
            name="Disabled Formatter",
            format_config="# Formatting",
            format_type="markdown",
            keywords=["test2"],
            enabled=False,
        )

        enabled_policy = await agent.policies.get(policy_id_enabled)
        disabled_policy = await agent.policies.get(policy_id_disabled)

        assert enabled_policy["enabled"] is True
        assert disabled_policy["enabled"] is False

    @pytest.mark.asyncio
    async def test_output_formatter_all_format_types(self):
        """Test output formatter with all format types"""
        agent = CugaAgent(tools=[read_data])

        policy_id_markdown = await agent.policies.add_output_formatter(
            name="Markdown Formatter",
            format_config="# Format as markdown",
            format_type="markdown",
            keywords=["markdown"],
        )

        policy_id_json = await agent.policies.add_output_formatter(
            name="JSON Formatter",
            format_config='{"result": "{{response}}"}',
            format_type="json_schema",
            keywords=["json"],
        )

        policy_id_direct = await agent.policies.add_output_formatter(
            name="Direct Formatter",
            format_config="Direct replacement text",
            format_type="direct",
            keywords=["direct"],
        )

        markdown_policy = await agent.policies.get(policy_id_markdown)
        json_policy = await agent.policies.get(policy_id_json)
        direct_policy = await agent.policies.get(policy_id_direct)

        assert markdown_policy["policy"].format_type == "markdown"
        assert json_policy["policy"].format_type == "json_schema"
        assert direct_policy["policy"].format_type == "direct"

    @pytest.mark.asyncio
    async def test_output_formatter_invalid_format_type(self):
        """Test output formatter with invalid format type"""
        agent = CugaAgent(tools=[calculate_sum])

        with pytest.raises(ValueError, match="format_type must be one of"):
            await agent.policies.add_output_formatter(
                name="Invalid Formatter",
                format_config="# Content",
                format_type="invalid_type",
                keywords=["test"],
            )

    @pytest.mark.asyncio
    async def test_output_formatter_list_and_get(self):
        """Test listing and getting output formatter policies"""
        agent = CugaAgent(tools=[send_email])

        policy_id = await agent.policies.add_output_formatter(
            name="Test Formatter",
            format_config="# Test formatting",
            format_type="markdown",
            keywords=["test"],
        )

        policies = await agent.policies.list()
        assert len(policies) == 1

        policy = await agent.policies.get(policy_id)
        assert policy is not None
        assert policy["id"] == policy_id
        assert policy["name"] == "Test Formatter"
        assert policy["type"] == "output_formatter"
        assert policy["policy"].format_config == "# Test formatting"


class TestSDKPolicyManagement:
    """Integration tests for policy management operations"""

    @pytest.mark.asyncio
    async def test_list_multiple_policy_types(self):
        """Test listing policies of different types"""
        agent = CugaAgent(tools=[send_email, delete_record])

        await agent.policies.add_intent_guard(
            name="Guard 1",
            keywords=["delete"],
            response="Blocked",
        )

        await agent.policies.add_playbook(
            name="Playbook 1",
            content="# Content",
            keywords=["onboard"],
        )

        await agent.policies.add_tool_approval(
            name="Approval 1",
            required_tools=["delete_record"],
        )

        await agent.policies.add_tool_guide(
            name="Guide 1",
            content="# Guidelines",
            target_tools=["send_email"],
        )

        await agent.policies.add_output_formatter(
            name="Formatter 1",
            format_config="# Formatting",
            format_type="markdown",
            keywords=["format"],
        )

        policies = await agent.policies.list()
        assert len(policies) == 5

        policy_types = {p["type"] for p in policies}
        assert "intent_guard" in policy_types
        assert "playbook" in policy_types
        assert "tool_approval" in policy_types
        assert "tool_guide" in policy_types
        assert "output_formatter" in policy_types

    @pytest.mark.asyncio
    async def test_delete_multiple_policies(self):
        """Test deleting multiple policies"""
        agent = CugaAgent(tools=[send_email])

        policy_id_1 = await agent.policies.add_intent_guard(
            name="Guard 1",
            keywords=["test1"],
            response="Blocked",
        )

        policy_id_2 = await agent.policies.add_playbook(
            name="Playbook 1",
            content="# Content",
            keywords=["test2"],
        )

        policies = await agent.policies.list()
        assert len(policies) == 2

        await agent.policies.delete(policy_id_1)
        policies = await agent.policies.list()
        assert len(policies) == 1

        await agent.policies.delete(policy_id_2)
        policies = await agent.policies.list()
        assert len(policies) == 0

    @pytest.mark.asyncio
    async def test_get_nonexistent_policy(self):
        """Test getting a policy that doesn't exist"""
        agent = CugaAgent(tools=[send_email])

        policy = await agent.policies.get("nonexistent_policy_id")
        assert policy is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_policy(self):
        """Test deleting a policy that doesn't exist"""
        agent = CugaAgent(tools=[send_email])

        success = await agent.policies.delete("nonexistent_policy_id")
        assert success is False

    @pytest.mark.asyncio
    async def test_custom_policy_ids(self):
        """Test creating policies with custom IDs"""
        agent = CugaAgent(tools=[send_email])

        custom_id = "my_custom_guard_123"
        policy_id = await agent.policies.add_intent_guard(
            name="Custom ID Guard",
            keywords=["test"],
            response="Blocked",
            policy_id=custom_id,
        )

        assert policy_id == custom_id

        policy = await agent.policies.get(custom_id)
        assert policy is not None
        assert policy["id"] == custom_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_tool_guards_from_json_scopes_to_imported_policy_ids(tmp_path, monkeypatch):
    import json

    from cuga.backend.server import tool_guard_generation

    agent = CugaAgent(tools=[send_email])

    unrelated_policy_id = await agent.policies.add_tool_guide(
        name="Existing Unrelated Guide",
        content="This guide already exists and must not be generated.",
        target_tools=["send_email"],
        description="Existing unrelated guide",
        policy_id="existing_unrelated_guide",
    )
    assert unrelated_policy_id == "existing_unrelated_guide"

    policies_path = tmp_path / "policies.json"
    policies_path.write_text(
        json.dumps(
            {
                "enablePolicies": True,
                "policies": [
                    {
                        "id": "imported_guide",
                        "policy_type": "tool_guide",
                        "name": "Imported Guide",
                        "description": "Imported guide",
                        "triggers": [{"type": "always"}],
                        "target_tools": ["send_email"],
                        "guide_content": "Only send approved emails.",
                        "enabled": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    captured_policy_ids = []

    async def fake_generate_tool_guards_for_policies(*, policy_system, policy_ids, generation_agent):
        captured_policy_ids.extend(policy_ids)
        return {
            "status": "ok",
            "generated": {
                "imported_guide": {
                    "status": "ok",
                    "policy_id": "imported_guide",
                    "results": [{"tool": "send_email", "status": "ok"}],
                }
            },
            "skipped": [],
            "errors": [],
        }

    monkeypatch.setattr(
        tool_guard_generation,
        "generate_tool_guards_for_policies",
        fake_generate_tool_guards_for_policies,
    )

    result = await agent.policies.generate_tool_guards_from_json(str(policies_path))

    assert captured_policy_ids == ["imported_guide"]
    assert result["status"] == "ok"
    assert result["import"]["count"] == 1
    assert result["source_policy_ids"] == ["imported_guide"]
    assert "existing_unrelated_guide" not in result["generated"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generate_tool_guards_from_json_clear_existing_replaces_storage(tmp_path, monkeypatch):
    import json

    from cuga.backend.server import tool_guard_generation

    agent = CugaAgent(tools=[send_email])

    await agent.policies.add_tool_guide(
        name="Existing Guide",
        content="This guide should be removed by clear_existing.",
        target_tools=["send_email"],
        description="Existing guide",
        policy_id="existing_guide",
    )

    policies_path = tmp_path / "policies.json"
    policies_path.write_text(
        json.dumps(
            {
                "policies": [
                    {
                        "id": "replacement_guide",
                        "policy_type": "tool_guide",
                        "name": "Replacement Guide",
                        "description": "Replacement guide",
                        "triggers": [{"type": "always"}],
                        "target_tools": ["send_email"],
                        "guide_content": "Replacement content.",
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    async def fake_generate_tool_guards_for_policies(*, policy_system, policy_ids, generation_agent):
        return {
            "status": "ok",
            "generated": {
                "replacement_guide": {
                    "status": "ok",
                    "policy_id": "replacement_guide",
                    "results": [{"tool": "send_email", "status": "ok"}],
                }
            },
            "skipped": [],
            "errors": [],
        }

    monkeypatch.setattr(
        tool_guard_generation,
        "generate_tool_guards_for_policies",
        fake_generate_tool_guards_for_policies,
    )

    result = await agent.policies.generate_tool_guards_from_json(
        str(policies_path),
        clear_existing=True,
    )

    assert result["status"] == "ok"
    assert result["source_policy_ids"] == ["replacement_guide"]
    assert result["import"]["count"] == 1
    assert result["generated"]["replacement_guide"]["status"] == "ok"
    # Verify the old policy was cleared from storage by clear_existing=True.
    # Access internal storage directly to avoid triggering a filesystem re-sync.
    assert await agent._policy_system.storage.get_policy("existing_guide") is None
