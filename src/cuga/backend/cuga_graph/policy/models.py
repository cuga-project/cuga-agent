"""Pydantic models for policy system."""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


class PolicyType(str, Enum):
    """Types of policies supported."""

    PLAYBOOK = "playbook"
    INTENT_GUARD = "intent_guard"
    TOOL_GUIDE = "tool_guide"
    TOOL_APPROVAL = "tool_approval"
    OUTPUT_FORMATTER = "output_formatter"
    CUSTOM = "custom"


class PolicyActionType(str, Enum):
    """Actions that can be taken when a policy matches."""

    GUIDE_PROMPT = "guide_prompt"
    BLOCK_INTENT = "block_intent"
    INJECT_CONTEXT = "inject_context"
    MODIFY_TOOLS = "modify_tools"
    TOOL_INJECT_DESCRIPTION = "tool_inject_description"
    TOOL_REQUIRE_APPROVAL = "tool_require_approval"
    FORMAT_OUTPUT = "format_output"
    REDIRECT = "redirect"
    LOG_ONLY = "log_only"


# Trigger Models
class NaturalLanguageTrigger(BaseModel):
    """Trigger based on natural language matching using semantic similarity."""

    type: Literal["natural_language"] = "natural_language"
    value: List[str] = Field(
        ..., description="Natural language descriptions of the trigger condition (multiple values supported)"
    )
    target: Optional[str] = Field(
        "intent",
        description="Target field containing the natural language text (intent, chat_messages, sub_task, agent_response)",
    )
    threshold: float = Field(
        0.7, ge=0.0, le=1.0, description="Similarity threshold for matching (0.0 to 1.0)"
    )


class KeywordTrigger(BaseModel):
    """Trigger based on keyword matching."""

    type: Literal["keyword"] = "keyword"
    value: List[str] = Field(..., description="List of keywords that trigger this policy")
    target: Optional[str] = Field(
        "intent",
        description="Target field containing text to search for keywords (intent, chat_messages, sub_task, agent_response)",
    )
    case_sensitive: bool = Field(False, description="Whether keyword matching is case-sensitive")
    operator: Literal["and", "or"] = Field(
        "and",
        description="Logical operator for multiple keywords: 'and' requires all keywords, 'or' requires any keyword",
    )


class AppTrigger(BaseModel):
    """Trigger based on application context."""

    type: Literal["app"] = "app"
    value: str = Field(..., description="Application name that triggers this policy")


class StateTrigger(BaseModel):
    """Trigger based on agent state conditions."""

    type: Literal["state"] = "state"
    key: str = Field(..., description="State key to check")
    value: str = Field(..., description="Expected value for the state key")
    operator: Literal["equals", "contains", "regex"] = Field("equals", description="Comparison operator")


class ToolTrigger(BaseModel):
    """Trigger based on tool usage."""

    type: Literal["tool"] = "tool"
    value: str = Field(..., description="Tool name that triggers this policy")
    stage: Literal["before", "after"] = Field(
        "before", description="Whether to trigger before or after tool execution"
    )


class AlwaysTrigger(BaseModel):
    """Trigger that always matches."""

    type: Literal["always"] = "always"


# Union type for all triggers
Trigger = Union[
    NaturalLanguageTrigger,
    KeywordTrigger,
    AppTrigger,
    StateTrigger,
    ToolTrigger,
    AlwaysTrigger,
]


# Policy-specific models
class PlaybookStep(BaseModel):
    """A single step in a playbook."""

    step_number: int = Field(..., description="Order of this step")
    instruction: str = Field(..., description="Instruction for this step")
    expected_outcome: Optional[str] = Field(None, description="Expected outcome of this step")
    tools_allowed: Optional[List[str]] = Field(None, description="Tools allowed for this step")


class Playbook(BaseModel):
    """Step-by-step instructions for completing a task."""

    type: Literal[PolicyType.PLAYBOOK] = PolicyType.PLAYBOOK
    id: str = Field(..., description="Unique identifier for this playbook")
    name: str = Field(..., description="Human-readable name")
    description: str = Field(..., description="Description of what this playbook does")
    triggers: List[Trigger] = Field(..., description="Conditions that activate this playbook")
    markdown_content: str = Field(..., description="Full markdown content of the playbook")
    steps: Optional[List[PlaybookStep]] = Field(
        None, description="Parsed steps (optional, can be derived from markdown)"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    priority: int = Field(0, description="Priority when multiple playbooks match (higher = more important)")
    enabled: bool = Field(True, description="Whether this playbook is active")

    @model_validator(mode='after')
    def validate_trigger_targets(self):
        """Ensure triggers target 'intent' or 'user_input' for Playbooks."""
        updated_triggers = []
        for trigger in self.triggers:
            if isinstance(trigger, (NaturalLanguageTrigger, KeywordTrigger)):
                if not trigger.target or trigger.target not in ("intent", "user_input"):
                    # Create new trigger with correct target
                    trigger_dict = trigger.model_dump()
                    trigger_dict['target'] = "intent"
                    if isinstance(trigger, NaturalLanguageTrigger):
                        updated_triggers.append(NaturalLanguageTrigger(**trigger_dict))
                    else:
                        updated_triggers.append(KeywordTrigger(**trigger_dict))
                else:
                    updated_triggers.append(trigger)
            else:
                updated_triggers.append(trigger)
        self.triggers = updated_triggers
        return self


class IntentGuardResponse(BaseModel):
    """Response configuration for intent guard."""

    response_type: Literal["json", "natural_language", "template"] = Field(
        ..., description="Type of response to return"
    )
    content: str = Field(..., description="Response content or template")
    status_code: Optional[int] = Field(None, description="HTTP status code if applicable")


class ToolGuard(BaseModel):
    """Guard configuration for a specific tool with compliance rules."""

    violating_examples: List[str] = Field(
        default_factory=list, description="Examples of violating usage patterns"
    )
    compliance_examples: List[str] = Field(
        default_factory=list, description="Examples of compliant usage patterns"
    )
    policy_code: str = Field(
        default="",
        description=(
            "Python code that validates tool usage compliance. "
            "This code is admin-authored and executed by the ToolGuard runtime in the "
            "tool-execution/backend context; in current CUGA Lite flows it runs in the "
            "backend service process with service-process privileges. "
            "Only trusted administrators with manage access should be allowed to modify policy code. "
            "Policy code should be reviewed for correctness, security, and performance."
        ),
    )


class IntentGuard(BaseModel):
    """Guard that intercepts intents and provides custom responses."""

    type: Literal[PolicyType.INTENT_GUARD] = PolicyType.INTENT_GUARD
    id: str = Field(..., description="Unique identifier for this intent guard")
    name: str = Field(..., description="Human-readable name")
    description: str = Field(..., description="Description of what this guard does")
    triggers: List[Trigger] = Field(..., description="Conditions that activate this guard")
    response: IntentGuardResponse = Field(..., description="Response configuration")
    allow_override: bool = Field(False, description="Whether user can override this guard")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    priority: int = Field(0, description="Priority when multiple guards match (higher = more important)")
    enabled: bool = Field(True, description="Whether this guard is active")

    @model_validator(mode='after')
    def validate_trigger_targets(self):
        """Ensure triggers target 'intent' or 'user_input' for IntentGuards."""
        updated_triggers = []
        for trigger in self.triggers:
            if isinstance(trigger, (NaturalLanguageTrigger, KeywordTrigger)):
                if not trigger.target or trigger.target not in ("intent", "user_input"):
                    # Create new trigger with correct target
                    trigger_dict = trigger.model_dump()
                    trigger_dict['target'] = "intent"
                    if isinstance(trigger, NaturalLanguageTrigger):
                        updated_triggers.append(NaturalLanguageTrigger(**trigger_dict))
                    else:
                        updated_triggers.append(KeywordTrigger(**trigger_dict))
                else:
                    updated_triggers.append(trigger)
            else:
                updated_triggers.append(trigger)
        self.triggers = updated_triggers
        return self


class ToolGuide(BaseModel):
    """Policy that enriches tool descriptions with additional markdown content."""

    type: Literal[PolicyType.TOOL_GUIDE] = PolicyType.TOOL_GUIDE
    id: str = Field(..., description="Unique identifier for this policy")
    name: str = Field(..., description="Human-readable name")
    description: str = Field(..., description="Description of what this guide does")
    triggers: List[Trigger] = Field(..., description="Conditions that activate this guide")
    target_tools: List[str] = Field(..., description="List of tool names to enrich (use '*' for all tools)")
    target_apps: Optional[List[str]] = Field(
        None,
        description=(
            "List of app names to enrich tools for (optional). Directly provided LangChain/runtime "
            "tools use the app name 'runtime_tools', so scoped policies for those tools must include it."
        ),
    )
    guide_content: str = Field(..., description="Markdown content to append to tool descriptions")
    tool_guards: Dict[str, ToolGuard] = Field(
        default_factory=dict,
        description="Optional guard configurations per tool (key: tool_name, value: ToolGuard)",
    )
    prepend: bool = Field(False, description="Whether to prepend content instead of appending")
    guards_enabled: bool = Field(True, description="Whether tool guard execution is active for this policy")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    priority: int = Field(0, description="Priority when multiple guides match (higher = more important)")
    enabled: bool = Field(True, description="Whether this guide is active")

    @field_validator("tool_guards", mode="before")
    @classmethod
    def default_tool_guards(cls, value):
        """Treat explicit null tool guard config as omitted."""
        return {} if value is None else value


class ToolApproval(BaseModel):
    """Policy that requires approval before executing specific tools.

    Note: ToolApproval policies are checked AFTER code generation, not during initial policy matching.
    They check if the generated code uses any of the specified tools/apps and require approval if so.
    """

    type: Literal[PolicyType.TOOL_APPROVAL] = PolicyType.TOOL_APPROVAL
    id: str = Field(..., description="Unique identifier for this policy")
    name: str = Field(..., description="Human-readable name")
    description: str = Field(..., description="Description of what this approval policy does")
    required_tools: List[str] = Field(
        ..., description="List of tool names that require approval (use '*' for all tools)"
    )
    required_apps: Optional[List[str]] = Field(
        None, description="List of app names whose tools require approval (optional)"
    )
    approval_message: Optional[str] = Field(
        None, description="Custom message to show when requesting approval"
    )
    show_code_preview: bool = Field(True, description="Whether to show code preview in approval request")
    auto_approve_after: Optional[int] = Field(
        None, description="Auto-approve after N seconds (None = no auto-approve)"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    priority: int = Field(
        0, description="Priority when multiple approval policies match (higher = more important)"
    )
    enabled: bool = Field(True, description="Whether this approval policy is active")


class OutputFormatter(BaseModel):
    """Policy that formats the final AI message output based on triggers.

    Note: OutputFormatter policies are checked AFTER the final AI message is generated.
    They check triggers against the last AI message content, then either:
    - For 'direct' type: Replace the response with format_config string directly (no LLM)
    - For 'markdown' or 'json_schema' types: Call an LLM to reformat the response
    """

    type: Literal[PolicyType.OUTPUT_FORMATTER] = PolicyType.OUTPUT_FORMATTER
    id: str = Field(..., description="Unique identifier for this policy")
    name: str = Field(..., description="Human-readable name")
    description: str = Field(..., description="Description of what this formatter does")
    triggers: List[Trigger] = Field(
        ..., description="Conditions that activate this formatter (checked against last AI message content)"
    )
    format_type: Literal["markdown", "json_schema", "direct"] = Field(
        "markdown",
        description="Type of formatting: markdown instructions, JSON schema, or direct string replacement",
    )
    format_config: str = Field(
        ...,
        description="Formatting configuration: markdown instructions, JSON schema string, or direct answer string (for 'direct' type)",
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    priority: int = Field(0, description="Priority when multiple formatters match (higher = more important)")
    enabled: bool = Field(True, description="Whether this formatter is active")

    @model_validator(mode='after')
    def validate_trigger_targets(self):
        """Ensure triggers target 'agent_response' for OutputFormatters."""
        updated_triggers = []
        for trigger in self.triggers:
            if isinstance(trigger, (NaturalLanguageTrigger, KeywordTrigger)):
                if not trigger.target or trigger.target != "agent_response":
                    # Create new trigger with correct target
                    trigger_dict = trigger.model_dump()
                    trigger_dict['target'] = "agent_response"
                    if isinstance(trigger, NaturalLanguageTrigger):
                        updated_triggers.append(NaturalLanguageTrigger(**trigger_dict))
                    else:
                        updated_triggers.append(KeywordTrigger(**trigger_dict))
                else:
                    updated_triggers.append(trigger)
            else:
                updated_triggers.append(trigger)
        self.triggers = updated_triggers
        return self


class CustomPolicy(BaseModel):
    """Custom policy for extensibility."""

    type: Literal[PolicyType.CUSTOM] = PolicyType.CUSTOM
    id: str = Field(..., description="Unique identifier for this policy")
    name: str = Field(..., description="Human-readable name")
    description: str = Field(..., description="Description of what this policy does")
    triggers: List[Trigger] = Field(..., description="Conditions that activate this policy")
    action_type: PolicyActionType = Field(..., description="Action to take when triggered")
    action_config: Dict[str, Any] = Field(..., description="Configuration for the action")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    priority: int = Field(0, description="Priority when multiple policies match (higher = more important)")
    enabled: bool = Field(True, description="Whether this policy is active")


# Union type for all policy types
Policy = Union[Playbook, IntentGuard, ToolGuide, ToolApproval, OutputFormatter, CustomPolicy]


class PolicyAction(BaseModel):
    """Action to take based on policy match."""

    action_type: PolicyActionType = Field(..., description="Type of action to take")
    policy_id: str = Field(..., description="ID of the policy that triggered this action")
    policy_type: PolicyType = Field(..., description="Type of policy that matched")
    content: Optional[str] = Field(None, description="Content to inject or display")
    modifications: Optional[Dict[str, Any]] = Field(
        None, description="Modifications to apply (tools, state, etc.)"
    )
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")


class PolicyMatch(BaseModel):
    """Result of policy matching."""

    matched: bool = Field(..., description="Whether any policy matched")
    policy: Optional[Policy] = Field(None, description="The matched policy")
    action: Optional[PolicyAction] = Field(None, description="Action to take")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="Confidence score of the match")
    reasoning: str = Field("", description="Explanation of why this policy matched or didn't match")
    trigger_details: Dict[str, Any] = Field(
        default_factory=dict, description="Details about which triggers matched"
    )
