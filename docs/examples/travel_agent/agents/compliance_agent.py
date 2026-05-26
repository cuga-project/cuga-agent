"""
compliance_agent — filters travel options against the corporate travel policy.

Uses the ``analyze_travel_compliance`` tool with a Tool Guide policy that is
added directly to the agent at module load time. The Tool Guide injects
role-based policy constraints into the tool's description so the LLM sees
the rules when it calls the tool.

The policy content is read from ``.cuga/tool_guides/`` and added via
``add_tool_guide`` — no auto-loading, no lazy initialization, no shared
global policy state. The policy is scoped to this agent only.

This module exposes a single ``compliance_agent`` CugaAgent instance used in
two ways:

1. **Standalone / SDK** (``main.py``): imported directly.
2. **CUGA UI** (``cuga start travel_agent``): referenced from
   ``config/supervisor_travel_agent.yaml`` via ``import_from``.
"""

import asyncio
from pathlib import Path

from cuga.sdk import CugaAgent
from docs.examples.travel_agent.tools.compliance import analyze_travel_compliance

_TOOL_GUIDES_DIR = Path(__file__).parent.parent / ".cuga" / "tool_guides"

_SPECIAL_INSTRUCTIONS = (
    "You are a corporate travel policy compliance specialist. "
    "Your policy rules are injected into the analyze_travel_compliance tool "
    "via the CUGA Tool Guide policy system. "
    "\n\n"
    "When asked to filter travel options:\n"
    "1. The task message contains flight and hotel data. Extract the full Flights section "
    "and the full Hotels section from the message and pass them as strings to the tool.\n"
    "2. ALWAYS call analyze_travel_compliance with the data from the task message:\n"
    "   analyze_travel_compliance(flights_json=<flights_text>, hotels_json=<hotels_text>, role='employee')\n"
    "   where <flights_text> is everything after 'Flights:' and before 'Hotels:', "
    "   and <hotels_text> is everything after 'Hotels:'.\n"
    "3. The tool will return a prompt with the data structured for policy evaluation.\n"
    "4. Apply the policy rules from the Tool Guide to filter the options.\n"
    "5. Return the results in the required output format:\n"
    "   - One sentence stating which role's policy was applied.\n"
    "   - A markdown table of compliant flights.\n"
    "   - A markdown table of compliant hotels.\n"
    "   - A list of rejected options with reasons.\n"
    "   - An approval notice if applicable.\n"
    "6. End with: 'Please select your preferred flight and hotel from the compliant options above.'"
)


async def _build_compliance_agent() -> CugaAgent:
    """Create the compliance agent and add the travel policy Tool Guide."""
    agent = CugaAgent(
        tools=[analyze_travel_compliance],
        # Disable auto-loading and filesystem sync — we add the policy directly below.
        auto_load_policies=False,
        filesystem_sync=False,
        special_instructions=_SPECIAL_INSTRUCTIONS,
    )
    agent.description = (
        "Filters flight and hotel options against the corporate travel policy. "
        "Policy rules are injected via the CUGA Tool Guide policy system. "
        "Returns two markdown tables of compliant options with rejection explanations."
    )

    # Add each role's Tool Guide directly — no .cuga folder auto-loading.
    # This keeps the policy scoped to this agent only (not the global policy system).
    for policy_file in sorted(_TOOL_GUIDES_DIR.glob("policy_*.md")):
        content = policy_file.read_text()
        # Strip YAML frontmatter — add_tool_guide expects plain markdown content
        if content.startswith("---"):
            end = content.index("---", 3)
            content = content[end + 3 :].strip()

        await agent.policies.add_tool_guide(
            name=f"Travel Policy — {policy_file.stem}",
            content=content,
            target_tools=["analyze_travel_compliance"],
            prepend=True,
            policy_id=f"tool_guide_{policy_file.stem}",
        )

    return agent


# Build the agent synchronously at module import time.
# asyncio.run() works when there is no running event loop (normal import context).
# When imported inside an already-running loop (e.g. during server startup via
# import_from), the policies will be added on the first invoke instead via the
# lazy _ensure_policy_system path — which is fine because auto_load_policies=False
# means no global state is touched.
try:
    compliance_agent = asyncio.run(_build_compliance_agent())
except RuntimeError as e:
    # Only catch the "already running event loop" case; re-raise other RuntimeErrors
    # so failures from _build_compliance_agent() (e.g., policy errors) are not hidden.
    if "cannot be called from a running event loop" not in str(e):
        raise
    # Already inside a running event loop (e.g. pytest-asyncio, Jupyter, cuga start).
    # Create the agent without policies; they will be added on first invoke
    # if needed, or the caller can await _build_compliance_agent() explicitly.
    compliance_agent = CugaAgent(
        tools=[analyze_travel_compliance],
        auto_load_policies=False,
        filesystem_sync=False,
        special_instructions=_SPECIAL_INSTRUCTIONS,
    )
    compliance_agent.description = "Filters flight and hotel options against the corporate travel policy."
