"""Policy agent for matching and executing policies based on context."""

import re
from typing import Any, Dict, List, Optional

import numpy as np
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.policy.models import (
    AlwaysTrigger,
    AppTrigger,
    IntentGuard,
    KeywordTrigger,
    NaturalLanguageTrigger,
    OutputFormatter,
    Playbook,
    Policy,
    PolicyAction,
    PolicyActionType,
    PolicyMatch,
    PolicyType,
    StateTrigger,
    ToolApproval,
    ToolGuide,
    ToolTrigger,
    Trigger,
)
from cuga.backend.cuga_graph.policy.storage import PolicyStorage


class PlaybookEnactment(BaseModel):
    """Result of playbook enactment with refined plan."""

    playbook_id: str = Field(..., description="ID of the playbook")
    playbook_name: str = Field(..., description="Name of the playbook")
    refined_plan: str = Field(..., description="Refined plan based on user progress")
    original_plan: str = Field(..., description="Original playbook markdown content")


class PolicyConflictResolution(BaseModel):
    """LLM response for resolving policy conflicts."""

    matched_policy_index: Optional[int] = Field(
        None,
        description="1-based index of the matched policy, or null if no match",
        ge=1,
    )
    confidence: float = Field(
        ...,
        description="Confidence score between 0.0 and 1.0",
        ge=0.0,
        le=1.0,
    )
    reasoning: str = Field(
        ...,
        description="Brief explanation of why this policy was chosen or why no match",
    )


class PolicyContext(BaseModel):
    """Context information for policy matching."""

    user_input: Optional[str] = Field(None, description="Current user input/intent")
    thread_id: Optional[str] = Field(None, description="Thread/session identifier")
    chat_messages: Optional[List[str]] = Field(None, description="Recent chat message history")
    current_agent: Optional[str] = Field(None, description="Current agent name")
    current_node: Optional[str] = Field(None, description="Current node in the graph")
    available_tools: Optional[List[str]] = Field(None, description="Available tools in current context")
    active_apps: Optional[List[str]] = Field(None, description="Active applications")
    state_data: Optional[Dict[str, Any]] = Field(None, description="Additional state data")
    sub_task: Optional[str] = Field(None, description="Current sub-task being executed")
    agent_response: Optional[str] = Field(None, description="Last agent response")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    def get_target_text(self, target: str) -> str:
        """
        Get text from the specified target field.

        For "agent_response" target, automatically combines user_input and agent_response
        to provide full context for OUTPUT_FORMATTER policies.

        Args:
            target: Target field name

        Returns:
            Text content from the target field
        """
        if target == "intent" or target == "user_input":
            return self.user_input or ""
        elif target == "chat_messages":
            return " ".join(self.chat_messages) if self.chat_messages else ""
        elif target == "sub_task":
            return self.sub_task or ""
        elif target == "agent_response":
            # For OUTPUT_FORMATTER policies, combine user input and agent response
            if self.user_input and self.agent_response:
                return f"User Input: {self.user_input}\n\nAgent Response: {self.agent_response}"
            return self.agent_response or ""
        else:
            return str(self.state_data.get(target, "")) if self.state_data else ""

    def get_query_text(self) -> Optional[str]:
        """
        Get the best text to use for vector search query.
        Prioritizes last chat message over user_input for better context.

        Returns:
            Query text for vector search, or None if no text available
        """
        # Try to get last user message from chat history
        if self.chat_messages and len(self.chat_messages) > 0:
            # Get the last message (most recent)
            last_message = self.chat_messages[-1]
            if last_message and last_message.strip():
                return last_message.strip()

        # Fallback to user_input
        if self.user_input and self.user_input.strip():
            return self.user_input.strip()

        return None

    def to_context_string(self) -> str:
        """Convert context to a string representation for LLM."""
        parts = []
        if self.user_input:
            parts.append(f"User Input: {self.user_input}")
        if self.thread_id:
            parts.append(f"Thread ID: {self.thread_id}")
        if self.chat_messages:
            parts.append(f"Recent Messages: {', '.join(self.chat_messages[-3:])}")
        if self.current_agent:
            parts.append(f"Current Agent: {self.current_agent}")
        if self.available_tools:
            parts.append(f"Available Tools: {', '.join(self.available_tools)}")
        if self.active_apps:
            parts.append(f"Active Apps: {', '.join(self.active_apps)}")
        if self.sub_task:
            parts.append(f"Current Sub-task: {self.sub_task}")
        return "\n".join(parts)


class PolicyAgent:
    """Agent for matching policies and determining actions based on context."""

    def __init__(
        self,
        storage: PolicyStorage,
        llm: Optional[BaseChatModel] = None,
        embedding_function: Optional[Any] = None,
    ):
        """
        Initialize PolicyAgent.

        Args:
            storage: PolicyStorage instance for retrieving policies
            llm: Language model for semantic matching and reasoning
            embedding_function: Function to generate embeddings for semantic search
        """
        self.storage = storage
        self.llm = llm
        self.embedding_function = embedding_function

    async def _check_trigger(self, trigger: Trigger, context: PolicyContext) -> tuple[bool, float, str]:
        """
        Check if a trigger matches the current context.

        Args:
            trigger: Trigger to check
            context: Current context

        Returns:
            Tuple of (matched, confidence, reasoning)
        """
        if isinstance(trigger, AlwaysTrigger):
            return True, 1.0, "Always trigger - matches all contexts"

        elif isinstance(trigger, KeywordTrigger):
            target_text = context.get_target_text(trigger.target)
            if not target_text:
                return False, 0.0, f"No text found in target field: {trigger.target}"

            if not trigger.case_sensitive:
                target_text = target_text.lower()
                keywords = [kw.lower() for kw in trigger.value]
            else:
                keywords = trigger.value

            matched_keywords = [kw for kw in keywords if kw in target_text]
            operator = getattr(trigger, 'operator', 'and')  # Default to 'and' for backward compatibility

            # Debug logging
            logger.info("🔍 Keyword Trigger Check:")
            logger.info(f"  Target text: '{target_text}'")
            logger.info(f"  Keywords: {keywords}")
            logger.info(f"  Operator: {operator}")
            logger.info(f"  Matched keywords: {matched_keywords}")

            if operator == 'or':
                # OR: Match if ANY keyword is found
                if matched_keywords:
                    confidence = len(matched_keywords) / len(keywords)
                    return (
                        True,
                        confidence,
                        f"Matched keywords (OR): {', '.join(matched_keywords)} in {trigger.target}",
                    )
                return False, 0.0, f"No keywords matched in {trigger.target}"
            else:
                # AND: Match if ALL keywords are found
                if len(matched_keywords) == len(keywords):
                    return (
                        True,
                        1.0,
                        f"Matched all keywords (AND): {', '.join(matched_keywords)} in {trigger.target}",
                    )
                elif matched_keywords:
                    # Some but not all keywords matched
                    return (
                        False,
                        len(matched_keywords) / len(keywords),
                        f"Only matched {len(matched_keywords)}/{len(keywords)} keywords (AND requires all): {', '.join(matched_keywords)} in {trigger.target}",
                    )
                return False, 0.0, f"No keywords matched in {trigger.target}"

        elif isinstance(trigger, AppTrigger):
            if context.active_apps and trigger.value in context.active_apps:
                return True, 1.0, f"App '{trigger.value}' is active"
            return False, 0.0, f"App '{trigger.value}' is not active"

        elif isinstance(trigger, ToolTrigger):
            if context.available_tools and trigger.value in context.available_tools:
                return True, 1.0, f"Tool '{trigger.value}' is available"
            return False, 0.0, f"Tool '{trigger.value}' is not available"

        elif isinstance(trigger, StateTrigger):
            if not context.state_data:
                return False, 0.0, "No state data available"

            state_value = context.state_data.get(trigger.key)
            if state_value is None:
                return False, 0.0, f"State key '{trigger.key}' not found"

            state_value_str = str(state_value)
            expected_value = trigger.value

            if trigger.operator == "equals":
                matched = state_value_str == expected_value
                reasoning = (
                    f"State '{trigger.key}' {'equals' if matched else 'does not equal'} '{expected_value}'"
                )
                return matched, 1.0 if matched else 0.0, reasoning

            elif trigger.operator == "contains":
                matched = expected_value in state_value_str
                reasoning = f"State '{trigger.key}' {'contains' if matched else 'does not contain'} '{expected_value}'"
                return matched, 1.0 if matched else 0.0, reasoning

            elif trigger.operator == "regex":
                try:
                    matched = bool(re.search(expected_value, state_value_str))
                    reasoning = f"State '{trigger.key}' {'matches' if matched else 'does not match'} regex '{expected_value}'"
                    return matched, 1.0 if matched else 0.0, reasoning
                except re.error as e:
                    return False, 0.0, f"Invalid regex pattern: {e}"

        elif isinstance(trigger, NaturalLanguageTrigger):
            target_text = context.get_target_text(trigger.target)
            if not target_text:
                return False, 0.0, f"No text found in target field: {trigger.target}"

            # Handle multiple values in the trigger (OR logic - match if any value matches)
            trigger_values = trigger.value if isinstance(trigger.value, list) else [trigger.value]
            if not trigger_values:
                return False, 0.0, "No natural language trigger values provided"

            # For NL triggers: use embedding-based similarity search
            if not self.embedding_function:
                return False, 0.0, "Embedding function not available for natural language trigger matching"

            best_similarity = 0.0
            best_matched_value = ""

            try:
                target_embedding = await self.embedding_function(target_text)

                for value in trigger_values:
                    value_embedding = await self.embedding_function(value)

                    # Calculate cosine similarity
                    target_vec = np.array(target_embedding)
                    value_vec = np.array(value_embedding)

                    # Normalize vectors
                    target_norm = (
                        target_vec / np.linalg.norm(target_vec)
                        if np.linalg.norm(target_vec) > 0
                        else target_vec
                    )
                    value_norm = (
                        value_vec / np.linalg.norm(value_vec) if np.linalg.norm(value_vec) > 0 else value_vec
                    )

                    # Cosine similarity
                    similarity = float(np.dot(target_norm, value_norm))

                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_matched_value = value

                matched = best_similarity >= trigger.threshold
                reasoning = f"Best match: '{best_matched_value}' - Embedding similarity: {best_similarity:.2f} (threshold: {trigger.threshold})"
                return matched, best_similarity, reasoning
            except Exception as e:
                logger.error(f"Error calculating embedding similarity for NL trigger: {e}")
                return False, 0.0, f"Error calculating embedding similarity: {str(e)}"

        return False, 0.0, f"Unknown trigger type: {type(trigger)}"

    async def _check_policy_triggers(
        self, policy: Policy, context: PolicyContext, skip_nl_triggers: bool = False
    ) -> tuple[bool, float, Dict[str, Any]]:
        """
        Check if all triggers for a policy match.

        Args:
            policy: Policy to check
            context: Current context
            skip_nl_triggers: If True, skip Natural Language triggers (already handled via conflict resolution)

        Returns:
            Tuple of (all_matched, avg_confidence, trigger_details)
        """
        trigger_details = {}
        confidences = []
        all_matched = True

        # Check all triggers (skip NL triggers if requested)
        for i, trigger in enumerate(policy.triggers):
            if skip_nl_triggers and isinstance(trigger, NaturalLanguageTrigger):
                logger.debug(f"  - Skipping NL trigger {i + 1} (already handled via conflict resolution)")
                continue

            matched, confidence, reasoning = await self._check_trigger(trigger, context)
            trigger_details[f"trigger_{i}"] = {
                "type": trigger.type,
                "matched": matched,
                "confidence": confidence,
                "reasoning": reasoning,
            }
            confidences.append(confidence)

            if not matched:
                all_matched = False

        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return all_matched, avg_confidence, trigger_details

    async def _create_policy_action(self, policy: Policy, confidence: float, reasoning: str) -> PolicyAction:
        """
        Create a PolicyAction based on the matched policy.

        Args:
            policy: Matched policy
            confidence: Confidence score
            reasoning: Reasoning for the match

        Returns:
            PolicyAction to execute
        """
        if isinstance(policy, Playbook):
            return PolicyAction(
                action_type=PolicyActionType.GUIDE_PROMPT,
                policy_id=policy.id,
                policy_type=PolicyType.PLAYBOOK,
                content=policy.markdown_content,
                modifications={"steps": [step.model_dump() for step in policy.steps] if policy.steps else []},
                metadata={
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "playbook_name": policy.name,
                },
            )

        elif isinstance(policy, IntentGuard):
            action_type = PolicyActionType.BLOCK_INTENT
            if policy.allow_override:
                action_type = PolicyActionType.LOG_ONLY

            return PolicyAction(
                action_type=action_type,
                policy_id=policy.id,
                policy_type=PolicyType.INTENT_GUARD,
                content=policy.response.content,
                modifications={
                    "response_type": policy.response.response_type,
                    "status_code": policy.response.status_code,
                    "allow_override": policy.allow_override,
                },
                metadata={
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "guard_name": policy.name,
                },
            )

        elif isinstance(policy, ToolGuide):
            return PolicyAction(
                action_type=PolicyActionType.TOOL_INJECT_DESCRIPTION,
                policy_id=policy.id,
                policy_type=PolicyType.TOOL_GUIDE,
                content=policy.guide_content,
                modifications={
                    "target_tools": policy.target_tools,
                    "target_apps": policy.target_apps,
                    "prepend": policy.prepend,
                },
                metadata={
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "guide_name": policy.name,
                },
            )

        elif isinstance(policy, ToolApproval):
            return PolicyAction(
                action_type=PolicyActionType.TOOL_REQUIRE_APPROVAL,
                policy_id=policy.id,
                policy_type=PolicyType.TOOL_APPROVAL,
                content=policy.approval_message,
                modifications={
                    "required_tools": policy.required_tools,
                    "required_apps": policy.required_apps,
                    "show_code_preview": policy.show_code_preview,
                    "auto_approve_after": policy.auto_approve_after,
                },
                metadata={
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "approval_policy_name": policy.name,
                },
            )

        elif isinstance(policy, OutputFormatter):
            return PolicyAction(
                action_type=PolicyActionType.FORMAT_OUTPUT,
                policy_id=policy.id,
                policy_type=PolicyType.OUTPUT_FORMATTER,
                content=policy.format_config,
                modifications={
                    "format_type": policy.format_type,
                },
                metadata={
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "formatter_name": policy.name,
                },
            )

        else:  # CustomPolicy
            return PolicyAction(
                action_type=policy.action_type,
                policy_id=policy.id,
                policy_type=PolicyType.CUSTOM,
                content=None,
                modifications=policy.action_config,
                metadata={
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "policy_name": policy.name,
                },
            )

    def _build_conflict_resolution_user_prompt(
        self,
        target: str,
        target_text: str,
        policies_text: List[str],
        context: PolicyContext,
        policy_types: Optional[List[PolicyType]] = None,
    ) -> str:
        """
        Build the user prompt for conflict resolution based on target, context, and policy types.

        Args:
            target: Target field being evaluated (e.g., "intent", "agent_response")
            target_text: The actual text from the target field
            policies_text: List of formatted policy descriptions
            context: Current policy context
            policy_types: Optional list of policy types being evaluated

        Returns:
            Formatted user prompt string
        """
        policies_section = f"Available Policies:\n{chr(10).join(policies_text)}"

        # Determine prompt format based on target and policy types
        if target == "agent_response":
            # For OUTPUT_FORMATTER policies, the target_text already contains both user input and agent response
            # Format: "User Input: ...\n\nAgent Response: ..."
            return f"""{target_text}

{policies_section}

Which policy (if any) best matches the context above?"""
        else:
            # For intent-based policies (INTENT_GUARD, PLAYBOOK, etc.)
            return f"""User Input: "{target_text}"

{policies_section}

Which policy (if any) best matches the user's intent?"""

    async def _resolve_nl_trigger_conflicts(
        self,
        policies_with_nl_triggers: List[tuple[Policy, List[NaturalLanguageTrigger]]],
        context: PolicyContext,
        target: str = "intent",
        target_text: Optional[str] = None,
        policy_types: Optional[List[PolicyType]] = None,
    ) -> Optional[tuple[Policy, float, str]]:
        """
        Use LLM to resolve conflicts when multiple policies have Natural Language triggers.

        Args:
            policies_with_nl_triggers: List of (policy, nl_triggers) tuples
            context: Current context
            target: Target field being evaluated (e.g., "intent", "agent_response")
            target_text: The actual text from the target field (includes combined user input + agent response for agent_response)

        Returns:
            Tuple of (best_policy, confidence, reasoning) or None if no match
        """
        logger.debug(f"🔧 Starting LLM conflict resolution for {len(policies_with_nl_triggers)} policies")

        # Use target_text if provided, otherwise fall back to query_text
        if target_text is None:
            target_text = context.get_target_text(target) or context.get_query_text()

        query_text = target_text  # Keep for backward compatibility in logging

        logger.debug(f"  - Target: '{target}'")
        logger.debug(f"  - Target text: '{query_text[:200] if query_text else None}...'")
        logger.debug(f"  - LLM available: {self.llm is not None}")
        logger.debug("  - Policies to resolve:")
        for i, (policy, nl_triggers) in enumerate(policies_with_nl_triggers, 1):
            logger.debug(f"    {i}. {policy.name} ({policy.type})")
            logger.debug(f"       Description: {policy.description[:100]}...")
            logger.debug(f"       NL Triggers: {[t.value for t in nl_triggers]}")

        if not self.llm or not query_text:
            # Fallback: return first policy
            logger.warning("⚠️  Cannot use LLM conflict resolution:")
            logger.warning(f"    - LLM available: {self.llm is not None}")
            logger.warning(f"    - Query text available: {query_text is not None}")
            if policies_with_nl_triggers:
                policy, triggers = policies_with_nl_triggers[0]
                logger.debug(f"  - Falling back to first policy: '{policy.name}'")
                return (
                    policy,
                    0.5,
                    "No LLM or query text available for conflict resolution, using first policy",
                )
            logger.debug("  - No policies to resolve, returning None")
            return None

        try:
            # Build prompt with all policies and their NL triggers
            policies_text = []
            for i, (policy, nl_triggers) in enumerate(policies_with_nl_triggers, 1):
                # Flatten all trigger values (each trigger can have multiple values)
                trigger_texts = []
                for trigger in nl_triggers:
                    if isinstance(trigger.value, list):
                        trigger_texts.extend(trigger.value)
                    else:
                        trigger_texts.append(trigger.value)
                policies_text.append(
                    f"{i}. **{policy.name}** ({policy.type})\n"
                    f"   Description: {policy.description}\n"
                    f"   Natural Language Triggers:\n" + "\n".join([f"      - {t}" for t in trigger_texts])
                )

            system_prompt = """You are a policy matching system that resolves conflicts when multiple policies could apply.

Your task: Analyze the user's input and determine which policy (if any) is the BEST match based on intent.

Guidelines:
- Focus on the CORE INTENT of the user's input
- Consider the policy descriptions and their natural language triggers
- If multiple policies could apply, choose the most specific/relevant one
- If NO policy truly matches the user's intent, set matched_policy_index to null
- Intent Guards (blocking/warning) should be prioritized if they match
- Playbooks (guidance) should match when user needs help with a process

Provide:
- matched_policy_index: 1-N (1-based index) or null if no match
- confidence: 0.0-1.0 (how confident you are in the match)
- reasoning: brief explanation of why this policy was chosen or why no match"""

            # Build user prompt using helper function
            user_prompt = self._build_conflict_resolution_user_prompt(
                target=target,
                target_text=query_text,
                policies_text=policies_text,
                context=context,
                policy_types=policy_types,
            )

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]

            logger.debug("  - Sending request to LLM for conflict resolution")
            logger.debug(f"    User prompt length: {len(user_prompt)} chars")
            logger.debug(f"    Number of policies in prompt: {len(policies_with_nl_triggers)}")

            # Use structured output for reliable JSON parsing.
            # method="json_schema" (matching OutputFormatter in enactment.py) is enforced
            # server-side by IBM Watsonx. The default (function_calling) returns None on
            # gpt-oss-120b when it emits no tool call, which raised AttributeError below and
            # silently dropped the policy match.
            structured_llm = self.llm.with_structured_output(PolicyConflictResolution, method="json_schema")
            result: PolicyConflictResolution = await structured_llm.ainvoke(messages)

            logger.debug("  - Received structured LLM response:")
            logger.debug(f"    - matched_policy_index: {result.matched_policy_index}")
            logger.debug(f"    - confidence: {result.confidence:.2f}")
            logger.debug(f"    - reasoning: {result.reasoning[:100]}...")

            matched_index = result.matched_policy_index
            confidence = result.confidence
            reasoning = result.reasoning

            if matched_index and 1 <= matched_index <= len(policies_with_nl_triggers):
                policy, nl_triggers = policies_with_nl_triggers[matched_index - 1]

                # Validate confidence against threshold(s) from the selected policy's triggers
                # Use minimum threshold (most strict) if multiple triggers exist
                thresholds = [trigger.threshold for trigger in nl_triggers if hasattr(trigger, 'threshold')]
                if thresholds:
                    min_threshold = min(thresholds)
                    if confidence < min_threshold:
                        logger.info(
                            f"❌ LLM conflict resolution confidence ({confidence:.2f}) "
                            f"below threshold ({min_threshold:.2f}) for policy '{policy.name}'"
                        )
                        logger.debug(
                            f"    - Confidence: {confidence:.2f}, Threshold: {min_threshold:.2f}, "
                            f"Policy: {policy.name}"
                        )
                        return None
                    logger.debug(f"    - Confidence {confidence:.2f} meets threshold {min_threshold:.2f}")

                logger.info(
                    f"✅ LLM resolved conflict: selected '{policy.name}' (confidence: {confidence:.2f})"
                )
                logger.debug(f"    - Selected policy index: {matched_index} (1-based)")
                return policy, confidence, f"LLM conflict resolution: {reasoning}"
            else:
                logger.info(
                    f"❌ LLM determined no policy matches (index {matched_index} out of range 1-{len(policies_with_nl_triggers)})"
                )
                return None

        except Exception as e:
            logger.error(f"❌ Error in LLM conflict resolution: {e}")
            logger.debug(f"    - Exception type: {type(e).__name__}")
            import traceback

            logger.debug(f"    - Traceback: {traceback.format_exc()}")
            # Fallback: return first policy
            if policies_with_nl_triggers:
                policy, _ = policies_with_nl_triggers[0]
                logger.debug(f"    - Falling back to first policy: '{policy.name}'")
                return policy, 0.5, f"Error in conflict resolution, using first policy: {e}"
            logger.debug("    - No policies available for fallback, returning None")
            return None

    async def _evaluate_keyword_triggered_policies(
        self, target: str, context: PolicyContext, policy_types: Optional[List[PolicyType]] = None
    ) -> Optional[tuple[Policy, float, str, Dict[str, Any]]]:
        """
        Evaluate all policies with keyword triggers matching the given target.

        For target="intent", prioritizes IntentGuard policies first regardless of priority value.

        Args:
            target: Target field to evaluate (e.g., "intent", "sub_task", "agent_response")
            context: Current policy context
            policy_types: Optional list of policy types to filter by. If None, excludes ToolApproval, ToolGuide, and OutputFormatter.

        Returns:
            Tuple of (matched_policy, confidence, reasoning, trigger_details) or None if no match
        """
        logger.debug(f"🔍 Evaluating keyword-triggered policies for target: '{target}'")

        if policy_types:
            # Get policies for each type and combine
            all_policies = []
            for policy_type in policy_types:
                policies = await self.storage.list_policies(policy_type=policy_type, enabled_only=True)
                all_policies.extend(policies)
        else:
            all_policies = await self.storage.list_policies(enabled_only=True)

        policies_with_keyword_triggers = []
        for policy in all_policies:
            if policy_types is None and isinstance(policy, (ToolApproval, ToolGuide, OutputFormatter)):
                continue

            keyword_triggers = [
                t
                for t in policy.triggers
                if isinstance(t, KeywordTrigger)
                and (t.target == target or (target == "intent" and t.target in ("intent", "user_input")))
            ]

            if keyword_triggers:
                policies_with_keyword_triggers.append((policy, keyword_triggers))

        if not policies_with_keyword_triggers:
            logger.debug(f"No policies with keyword triggers for target '{target}'")
            return None

        logger.debug(
            f"Found {len(policies_with_keyword_triggers)} policies with keyword triggers for target '{target}'"
        )

        if target == "intent":
            intent_guards = [(p, t) for p, t in policies_with_keyword_triggers if isinstance(p, IntentGuard)]
            other_policies = [
                (p, t) for p, t in policies_with_keyword_triggers if not isinstance(p, IntentGuard)
            ]

            policies_to_check = intent_guards + other_policies
            logger.debug(f"Prioritizing {len(intent_guards)} Intent Guards for intent target")
        else:
            policies_to_check = policies_with_keyword_triggers

        best_match = None
        best_confidence = 0.0
        best_reasoning = ""
        best_trigger_details = {}

        for policy, keyword_triggers in policies_to_check:
            all_matched, confidence, trigger_details = await self._check_policy_triggers(policy, context)

            if all_matched and confidence > best_confidence:
                best_match = policy
                best_confidence = confidence
                best_reasoning = f"Keyword-triggered policy '{policy.name}' matched for target '{target}'"
                best_trigger_details = trigger_details
                logger.debug(f"✅ Keyword match: '{policy.name}' (confidence: {confidence:.2f})")

        if best_match:
            logger.info(f"Keyword-triggered policy matched: '{best_match.name}' for target '{target}'")
            return best_match, best_confidence, best_reasoning, best_trigger_details

        return None

    async def _evaluate_natural_language_policies(
        self, target: str, context: PolicyContext, policy_types: Optional[List[PolicyType]] = None
    ) -> Optional[tuple[Policy, float, str, Dict[str, Any]]]:
        """
        Evaluate policies with natural language triggers for the given target.

        Performs semantic search to find candidate policies, then checks ALL policies
        with NL triggers for the target. Always calls conflict resolution LLM even if
        only 1 policy is found to validate the match.

        Args:
            target: Target field to evaluate (e.g., "intent", "sub_task", "agent_response")
            context: Current policy context
            policy_types: Optional list of policy types to filter by. If None, excludes ToolApproval, ToolGuide, and OutputFormatter.

        Returns:
            Tuple of (matched_policy, confidence, reasoning, trigger_details) or None if no match
        """
        logger.debug(f"🔍 Evaluating natural language policies for target: '{target}'")

        query_text = context.get_target_text(target)
        if not query_text:
            logger.debug(f"No query text available for target '{target}'")
            return None

        if policy_types:
            # Get policies for each type and combine
            all_policies = []
            for policy_type in policy_types:
                policies = await self.storage.list_policies(policy_type=policy_type, enabled_only=True)
                all_policies.extend(policies)
        else:
            all_policies = await self.storage.list_policies(enabled_only=True)

        policies_with_nl_triggers = []
        for policy in all_policies:
            if policy_types is None and isinstance(policy, (ToolApproval, ToolGuide, OutputFormatter)):
                continue

            nl_triggers = [
                t
                for t in policy.triggers
                if isinstance(t, NaturalLanguageTrigger)
                and (t.target == target or (target == "intent" and t.target in ("intent", "user_input")))
            ]

            if nl_triggers:
                policies_with_nl_triggers.append((policy, nl_triggers))

        if not policies_with_nl_triggers:
            logger.debug(f"No policies with NL triggers for target '{target}'")
            return None

        logger.debug(
            f"Found {len(policies_with_nl_triggers)} policies with NL triggers for target '{target}'"
        )

        if self.embedding_function and query_text:
            try:
                query_embedding = await self.embedding_function(query_text)
                # For vector search with multiple policy types, search without type filter and filter results
                vector_candidates = await self.storage.search_policies(
                    query_embedding=query_embedding,
                    limit=20,
                    enabled_only=True,
                )
                # Filter by policy_types if specified
                if policy_types:
                    policy_type_set = set(policy_types)
                    filtered_candidates = []
                    for policy, score in vector_candidates:
                        policy_type = PolicyType(policy.type) if hasattr(policy, 'type') else None
                        if policy_type and policy_type in policy_type_set:
                            filtered_candidates.append((policy, score))
                    vector_candidates = filtered_candidates
                vector_policy_ids = {policy.id for policy, _ in vector_candidates}

                policies_with_nl_triggers.sort(
                    key=lambda x: (x[0].id in vector_policy_ids, x[0].priority), reverse=True
                )
                logger.debug(
                    f"Prioritized {sum(1 for p, _ in policies_with_nl_triggers if p.id in vector_policy_ids)} policies from vector search"
                )
            except Exception as e:
                logger.debug(f"Vector search failed (non-critical): {e}")

        if not self.llm:
            logger.warning("No LLM available for NL policy evaluation")
            return None

        resolution = await self._resolve_nl_trigger_conflicts(
            policies_with_nl_triggers,
            context,
            target=target,
            target_text=query_text,
            policy_types=policy_types,
        )
        if not resolution:
            logger.debug("LLM conflict resolution returned no match")
            return None

        resolved_policy, confidence, reasoning = resolution
        logger.debug(f"LLM resolved to: '{resolved_policy.name}' (confidence: {confidence:.2f})")

        # Get NL triggers for the resolved policy to check threshold
        nl_triggers_for_policy = [
            t
            for t in resolved_policy.triggers
            if isinstance(t, NaturalLanguageTrigger)
            and (t.target == target or (target == "intent" and t.target in ("intent", "user_input")))
        ]

        # Validate confidence against threshold(s) from the policy's NL triggers
        # Use minimum threshold (most strict) if multiple triggers exist
        if nl_triggers_for_policy:
            thresholds = [
                trigger.threshold for trigger in nl_triggers_for_policy if hasattr(trigger, 'threshold')
            ]
            if thresholds:
                min_threshold = min(thresholds)
                if confidence < min_threshold:
                    logger.info(
                        f"❌ LLM conflict resolution confidence ({confidence:.2f}) "
                        f"below threshold ({min_threshold:.2f}) for policy '{resolved_policy.name}'"
                    )
                    logger.debug(
                        f"    - Confidence: {confidence:.2f}, Threshold: {min_threshold:.2f}, "
                        f"Policy: {resolved_policy.name}"
                    )
                    return None
                logger.debug(
                    f"✅ Confidence {confidence:.2f} meets threshold {min_threshold:.2f} for policy '{resolved_policy.name}'"
                )

        all_matched, full_confidence, trigger_details = await self._check_policy_triggers(
            resolved_policy, context, skip_nl_triggers=True
        )

        if not all_matched:
            logger.debug(f"Resolved policy '{resolved_policy.name}' did not match all non-NL triggers")
            return None

        # Final confidence must also meet the threshold
        final_confidence = max(confidence, full_confidence)
        if nl_triggers_for_policy:
            thresholds = [
                trigger.threshold for trigger in nl_triggers_for_policy if hasattr(trigger, 'threshold')
            ]
            if thresholds:
                min_threshold = min(thresholds)
                if final_confidence < min_threshold:
                    logger.info(
                        f"❌ Final confidence ({final_confidence:.2f}) "
                        f"below threshold ({min_threshold:.2f}) for policy '{resolved_policy.name}'"
                    )
                    return None

        final_reasoning = f"NL-triggered policy (LLM-validated): {reasoning}"

        logger.info(f"NL-triggered policy matched: '{resolved_policy.name}' for target '{target}'")
        return resolved_policy, final_confidence, final_reasoning, trigger_details

    async def match_policy(
        self, context: PolicyContext, target: str = "intent", policy_types: Optional[List[PolicyType]] = None
    ) -> PolicyMatch:
        """
        Find and match the best policy for the given context.

        Uses helper functions to evaluate keyword and natural language triggered policies,
        prioritizing Intent Guards over Playbooks.

        Args:
            context: Current context information
            target: Target field to evaluate (e.g., "intent", "sub_task", "agent_response").
                    Defaults to "intent".
            policy_types: Optional list of policy types to filter by (e.g., [PolicyType.OUTPUT_FORMATTER]).
                        If None and target="intent", matches Intent Guards and Playbooks.
                        If None and target!="intent", excludes ToolApproval, ToolGuide, and OutputFormatter.

        Returns:
            PolicyMatch with the best matching policy and action
        """
        try:
            logger.debug(f"🔍 Starting policy matching for target: '{target}'")

            # Default policy types for intent target
            if policy_types is None and target == "intent":
                policy_types = [PolicyType.INTENT_GUARD, PolicyType.PLAYBOOK]
                logger.debug(f"  - Using default policy types for intent: {policy_types}")
            elif policy_types:
                logger.debug(f"  - Filtering by policy types: {policy_types}")

            logger.debug(f"  - user_input: {context.user_input}")
            logger.debug(f"  - chat_messages: {context.chat_messages}")

            # For intent target, use get_query_text() which prioritizes last chat message
            # For other targets, use get_target_text() to get the specific field
            if target == "intent":
                query_text = context.get_query_text()
            else:
                query_text = context.get_target_text(target)

            if not query_text:
                return PolicyMatch(
                    matched=False,
                    reasoning=f"No query text available for target '{target}'",
                )

            keyword_match = await self._evaluate_keyword_triggered_policies(target, context, policy_types)
            nl_match = await self._evaluate_natural_language_policies(target, context, policy_types)

            best_match = None
            best_confidence = 0.0
            best_reasoning = ""
            best_trigger_details = {}

            candidates = []
            if keyword_match:
                candidates.append(keyword_match)
            if nl_match:
                candidates.append(nl_match)

            for policy, confidence, reasoning, trigger_details in candidates:
                if isinstance(policy, IntentGuard):
                    if not best_match or not isinstance(best_match, IntentGuard):
                        best_match = policy
                        best_confidence = confidence
                        best_reasoning = reasoning
                        best_trigger_details = trigger_details
                        logger.debug(f"Intent Guard '{policy.name}' takes precedence")
                    elif confidence > best_confidence:
                        best_match = policy
                        best_confidence = confidence
                        best_reasoning = reasoning
                        best_trigger_details = trigger_details
                        logger.debug(f"Intent Guard '{policy.name}' has higher confidence")
                elif not best_match or (
                    not isinstance(best_match, IntentGuard) and confidence > best_confidence
                ):
                    best_match = policy
                    best_confidence = confidence
                    best_reasoning = reasoning
                    best_trigger_details = trigger_details
                    logger.debug(f"Policy '{policy.name}' selected (confidence: {confidence:.2f})")

            if best_match:
                logger.info(f"Policy matched: '{best_match.name}' (confidence: {best_confidence:.2f})")
                action = await self._create_policy_action(best_match, best_confidence, best_reasoning)

                # Clamp confidence to PolicyMatch's [0.0, 1.0] bound. When both
                # keyword and NL triggers fire on the same policy, the combined
                # score can land at 1.0000000000000002 (float rounding); without
                # the clamp PolicyMatch validation raises and the whole match
                # is silently lost inside the `except` below.
                return PolicyMatch(
                    matched=True,
                    policy=best_match,
                    action=action,
                    confidence=min(1.0, max(0.0, best_confidence)),
                    reasoning=best_reasoning,
                    trigger_details=best_trigger_details,
                )

            return PolicyMatch(
                matched=False,
                reasoning="No policies matched the current context",
            )

        except Exception as e:
            logger.error(f"Error matching policy: {e}")
            return PolicyMatch(
                matched=False,
                reasoning=f"Error during policy matching: {str(e)}",
            )

    async def match_policies_by_type(
        self, context: PolicyContext, policy_type: PolicyType
    ) -> List[PolicyMatch]:
        """
        Find all matching policies of a specific type.

        Args:
            context: Current context information
            policy_type: Type of policies to match

        Returns:
            List of PolicyMatch objects
        """
        try:
            # Get query text (prioritizes last chat message over user_input)
            query_text = context.get_query_text()

            # Get policies of specific type
            if self.embedding_function and query_text:
                query_embedding = await self.embedding_function(query_text)
                candidates = await self.storage.search_policies(
                    query_embedding=query_embedding,
                    limit=20,
                    policy_type=policy_type,
                    enabled_only=True,
                )
                policies = [policy for policy, _ in candidates]
            else:
                policies = await self.storage.list_policies(
                    policy_type=policy_type,
                    enabled_only=True,
                )

            matches = []
            for policy in policies:
                all_matched, confidence, trigger_details = await self._check_policy_triggers(policy, context)

                if all_matched:
                    reasoning = f"Policy '{policy.name}' matched with confidence {confidence:.2f}"
                    action = await self._create_policy_action(policy, confidence, reasoning)

                    matches.append(
                        PolicyMatch(
                            matched=True,
                            policy=policy,
                            action=action,
                            confidence=confidence,
                            reasoning=reasoning,
                            trigger_details=trigger_details,
                        )
                    )

            # Sort by confidence (descending)
            matches.sort(key=lambda x: x.confidence, reverse=True)
            return matches

        except Exception as e:
            logger.error(f"Error matching policies by type: {e}")
            return []

    async def explain_match(self, policy_match: PolicyMatch) -> str:
        """
        Generate a human-readable explanation of why a policy matched.

        Args:
            policy_match: PolicyMatch to explain

        Returns:
            Explanation string
        """
        if not policy_match.matched:
            return f"No policy matched. Reason: {policy_match.reasoning}"

        explanation_parts = [
            f"Policy Matched: {policy_match.policy.name}",
            f"Type: {policy_match.policy.type}",
            f"Confidence: {policy_match.confidence:.2%}",
            f"Action: {policy_match.action.action_type.value}",
            f"\nReasoning: {policy_match.reasoning}",
            "\nTrigger Details:",
        ]

        for trigger_name, details in policy_match.trigger_details.items():
            explanation_parts.append(
                f"  - {trigger_name} ({details['type']}): "
                f"{'✓' if details['matched'] else '✗'} "
                f"[{details['confidence']:.2f}] - {details['reasoning']}"
            )

        return "\n".join(explanation_parts)

    async def enact_playbook(self, playbook: Playbook, context: PolicyContext) -> PlaybookEnactment:
        """
        Refine playbook plan based on user's current progress.

        Analyzes user intent and conversation to determine if they've already
        completed part of the task, then returns a refined plan.

        Args:
            playbook: The playbook to enact
            context: Current context with user input and chat messages

        Returns:
            PlaybookEnactment with refined plan
        """
        if not self.llm:
            logger.warning("No LLM available for playbook enactment, returning original plan")
            return PlaybookEnactment(
                playbook_id=playbook.id,
                playbook_name=playbook.name,
                refined_plan=playbook.markdown_content,
                original_plan=playbook.markdown_content,
            )

        refined_plan = await self._refine_playbook_plan(playbook, context)

        logger.info(f"Playbook {playbook.name} enacted and refined")

        return PlaybookEnactment(
            playbook_id=playbook.id,
            playbook_name=playbook.name,
            refined_plan=refined_plan,
            original_plan=playbook.markdown_content,
        )

    async def _refine_playbook_plan(self, playbook: Playbook, context: PolicyContext) -> str:
        """
        Refine playbook plan based on user's progress.

        Simple approach: Give LLM the playbook and conversation, ask it to
        refine the plan based on what the user has already done.

        Args:
            playbook: The playbook to refine
            context: Current context with user input and chat history

        Returns:
            Refined plan as markdown string
        """
        if not self.llm:
            return playbook.markdown_content

        try:
            conversation_history = ""
            if context.chat_messages:
                recent_messages = context.chat_messages[-15:]
                conversation_history = "\n".join([f"- {msg}" for msg in recent_messages])

            system_prompt = """You are a task planning assistant.

Your task: Refine a playbook/plan based on what the user has already accomplished.

Guidelines:
- Review the conversation to understand what the user has already done
- If they've completed some steps, acknowledge that and focus on remaining work
- If they're just starting, provide the full plan
- Keep the same structure and format as the original playbook
- Be concise and actionable
- Use markdown formatting

Output: A refined version of the playbook that focuses on what's left to do."""

            user_prompt = f"""User Intent: {context.user_input or "N/A"}

Conversation History:
{conversation_history if conversation_history else "No prior conversation"}

---

Original Playbook:

{playbook.markdown_content}

---

Based on the conversation, refine this playbook to focus on what the user still needs to do:"""

            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]

            response = await self.llm.ainvoke(messages)
            refined_plan = response.content.strip()

            return refined_plan

        except Exception as e:
            logger.error(f"Error refining playbook plan: {e}")
            return playbook.markdown_content

    async def check_tool_guide_policies(self, context: PolicyContext) -> List[PolicyMatch]:
        """
        Check all Tool Guide policies that match the current context.

        Tool Guide policies are applied independently of Intent Guards and Playbooks.
        Multiple guide policies can be applied simultaneously.

        Args:
            context: Current policy context

        Returns:
            List of PolicyMatch objects for all matching Tool Guide policies
        """
        logger.debug("Checking Tool Guide policies")

        # Get all ToolGuide policies from storage
        guide_policies = await self.storage.list_policies(
            policy_type=PolicyType.TOOL_GUIDE, enabled_only=True, limit=100
        )

        logger.debug(f"Found {len(guide_policies)} enabled Tool Guide policies")

        if not guide_policies:
            return []

        # Check each policy and collect all matches
        matches = []

        for policy in guide_policies:
            logger.debug(f"Checking guide policy '{policy.name}'")

            # Check if this policy's triggers match
            all_matched, confidence, trigger_details = await self._check_policy_triggers(policy, context)

            if all_matched:
                logger.info(f"Tool Guide policy '{policy.name}' matched (confidence: {confidence:.2f})")

                # Create a PolicyMatch for this guide
                action = await self._create_policy_action(
                    policy, confidence, f"Guide policy '{policy.name}' matched"
                )
                matches.append(
                    PolicyMatch(
                        matched=True,
                        policy=policy,
                        action=action,
                        confidence=confidence,
                        reasoning="Tool guide policy matched",
                        trigger_details=trigger_details,
                    )
                )

        logger.debug(f"Matched {len(matches)} Tool Guide policies")
        return matches

    async def check_tool_approval_for_code(self, code: str, context: PolicyContext) -> Optional[PolicyMatch]:
        """
        Check if generated code requires tool approval.

        This method is called AFTER code generation to check if any ToolApproval policies
        apply to the tools used in the code.

        Args:
            code: The generated code to check
            context: Current policy context

        Returns:
            PolicyMatch if approval is required, None otherwise
        """
        logger.debug(f"check_tool_approval_for_code called with code: {code[:100]}...")

        # Get all ToolApproval policies from storage
        tool_approval_policies = await self.storage.list_policies(
            policy_type=PolicyType.TOOL_APPROVAL, enabled_only=True, limit=100
        )

        logger.debug(f"Found {len(tool_approval_policies)} enabled ToolApproval policies")

        if not tool_approval_policies:
            logger.debug("No ToolApproval policies configured")
            return None

        # Check each ToolApproval policy to see if it applies to the code
        best_match = None
        best_priority = -1
        matched_tool_names = []

        for policy in tool_approval_policies:
            logger.debug(
                f"Checking policy '{policy.name}' (required_tools: {policy.required_tools}, required_apps: {policy.required_apps})"
            )

            # Check if this policy's tools/apps are used in the code
            applies, tool_names = self._check_code_uses_tools(
                code, policy.required_tools, policy.required_apps
            )

            logger.debug(f"Policy '{policy.name}' applies: {applies}, matched tools: {tool_names}")

            if applies:
                # Use priority to determine which policy takes precedence
                if policy.priority > best_priority:
                    best_match = policy
                    best_priority = policy.priority
                    matched_tool_names = tool_names
                    logger.info(
                        f"ToolApproval policy '{policy.name}' applies to generated code (tools: {tool_names})"
                    )

        if best_match:
            logger.info(f"Creating PolicyMatch for ToolApproval policy '{best_match.name}'")
            # Create a PolicyMatch for the approval requirement
            action = await self._create_policy_action(
                best_match, 1.0, f"Code uses tools requiring approval: {best_match.name}"
            )
            return PolicyMatch(
                matched=True,
                policy=best_match,
                action=action,
                confidence=1.0,
                reasoning="Generated code uses tools that require approval",
                trigger_details={"matched_tools": matched_tool_names},
            )

        logger.debug("No ToolApproval policy matched the code")
        return None

    def _check_code_uses_tools(
        self, code: str, required_tools: List[str], required_apps: Optional[List[str]]
    ) -> tuple[bool, List[str]]:
        """
        Check if code uses any of the specified tools or apps.

        Args:
            code: Generated code to check
            required_tools: List of tool names (or '*' for all)
            required_apps: List of app names (optional)

        Returns:
            Tuple of (applies, matched_tool_names)
        """
        import re

        # If '*' is in required_tools, any tool usage triggers approval
        if '*' in required_tools:
            # Look for common function call patterns
            function_calls = re.findall(r'(\w+)\s*\(', code)
            if function_calls:
                return True, function_calls[:5]  # Return first 5 for logging

        matched_tools = []

        # Check for specific tool names
        for tool_name in required_tools:
            if tool_name != '*' and tool_name in code:
                matched_tools.append(tool_name)

        # Check for app-specific tools (e.g., "digital_sales_")
        if required_apps:
            for app_name in required_apps:
                # Look for functions that start with app_name_
                app_pattern = rf'\b{re.escape(app_name)}_\w+\s*\('
                if re.search(app_pattern, code):
                    app_tools = re.findall(rf'\b({re.escape(app_name)}_\w+)\s*\(', code)
                    matched_tools.extend(app_tools)

        return len(matched_tools) > 0, matched_tools
