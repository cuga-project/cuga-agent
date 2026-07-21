from __future__ import annotations

import logging
from typing import Any, Dict

from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
from cuga.backend.cuga_graph.policy.models import ToolGuide
from cuga.config import settings
from cuga.sdk import CugaAgent

logger = logging.getLogger(__name__)


ToolGuardGenerationResult = Dict[str, Any]


def build_tool_guard_generation_agent(
    *,
    policy_system: PolicyConfigurable,
    tool_provider: Any,
    model: Any = None,
) -> CugaAgent:
    return CugaAgent(
        tool_provider=tool_provider,
        policy_system=policy_system,
        model=model,
        cuga_folder=settings.policy.cuga_folder,
        auto_load_policies=False,
        filesystem_sync=False,
    )


def _concrete_target_tools(policy: ToolGuide) -> list[str]:
    target_tools = policy.target_tools if policy.target_tools else []
    if not target_tools or target_tools == ["*"] or "*" in target_tools:
        raise ValueError("Select specific target tools to generate a guard")
    return target_tools


async def generate_tool_guards_for_policy(
    *,
    policy_system: PolicyConfigurable,
    policy_id: str,
    generation_agent: CugaAgent,
) -> ToolGuardGenerationResult:
    existing_policy = await policy_system.storage.get_policy(policy_id)
    if existing_policy is None:
        raise LookupError(f"Policy '{policy_id}' was not found")
    if not isinstance(existing_policy, ToolGuide):
        raise TypeError(f"Policy '{policy_id}' is not a Tool Guide policy")
    if not existing_policy.enabled:
        raise ValueError(f"Policy '{policy_id}' is disabled")

    target_tools = _concrete_target_tools(existing_policy)
    results: list[dict[str, Any]] = []

    for tool_name in target_tools:
        try:
            (
                violating_examples,
                compliance_examples,
            ) = await generation_agent.policies.generate_tool_guard_examples(
                policy_id=policy_id,
                target_tool=tool_name,
            )
            await generation_agent.policies.update_tool_guard(
                policy_id=policy_id,
                tool_guards={
                    tool_name: {
                        "violating_examples": violating_examples,
                        "compliance_examples": compliance_examples,
                    }
                },
            )
            policy_code = await generation_agent.policies.generate_tool_guard_code(
                policy_id=policy_id,
                target_tool=tool_name,
            )
            await generation_agent.policies.update_tool_guard(
                policy_id=policy_id,
                tool_guards={tool_name: {"policy_code": policy_code}},
            )
            results.append({"tool": tool_name, "status": "ok"})
        except Exception:
            logger.exception("ToolGuard generation failed for tool %s in policy %s", tool_name, policy_id)
            results.append(
                {"tool": tool_name, "status": "error", "message": "ToolGuard generation failed for this tool"}
            )

    top_level_status = "ok" if any(result["status"] == "ok" for result in results) else "error"
    return {"status": top_level_status, "policy_id": policy_id, "results": results}


def _batch_status(
    *,
    generated: dict[str, ToolGuardGenerationResult],
    skipped: list[dict[str, str]],
    errors: list[str],  # reserved for future batch-level errors
) -> str:
    if not generated:
        return "ok" if not skipped and not errors else "partial"

    successful_policy_count = sum(1 for result in generated.values() if result.get("status") == "ok")
    failed_policy_count = sum(1 for result in generated.values() if result.get("status") != "ok")

    if successful_policy_count == 0:
        return "error"
    if failed_policy_count or skipped or errors:
        return "partial"
    return "ok"


async def generate_tool_guards_for_policies(
    *,
    policy_system: PolicyConfigurable,
    policy_ids: list[str],
    generation_agent: CugaAgent,
) -> ToolGuardGenerationResult:
    """Generate ToolGuards sequentially for eligible ToolGuide policies."""
    generated: dict[str, ToolGuardGenerationResult] = {}
    skipped: list[dict[str, str]] = []
    errors: list[str] = []

    for policy_id in policy_ids:
        existing_policy = await policy_system.storage.get_policy(policy_id)
        if existing_policy is None:
            skipped.append({"policy_id": policy_id, "reason": "missing"})
            continue
        if not isinstance(existing_policy, ToolGuide):
            skipped.append({"policy_id": policy_id, "reason": "not_tool_guide"})
            continue
        if not existing_policy.enabled:
            skipped.append({"policy_id": policy_id, "reason": "disabled"})
            continue
        try:
            _concrete_target_tools(existing_policy)
        except ValueError:
            skipped.append({"policy_id": policy_id, "reason": "no_concrete_target_tools"})
            continue

        try:
            generated[policy_id] = await generate_tool_guards_for_policy(
                policy_system=policy_system,
                policy_id=policy_id,
                generation_agent=generation_agent,
            )
        except Exception as exc:
            logger.exception("ToolGuard batch generation failed for policy %s", policy_id)
            generated[policy_id] = {
                "status": "error",
                "policy_id": policy_id,
                "results": [],
                "message": str(exc),
            }

    return {
        "status": _batch_status(generated=generated, skipped=skipped, errors=errors),
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
    }
