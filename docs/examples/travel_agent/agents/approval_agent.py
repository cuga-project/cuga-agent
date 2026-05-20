"""
approval_agent — sends a travel-approval request to the manager via Slack.

This module exposes a single ``approval_agent`` CugaAgent instance that is
used in two ways:

1. **Standalone / SDK** (``main.py``): imported directly.
2. **CUGA UI** (``cuga start travel_agent``): referenced from
   ``config/supervisor_travel_agent.yaml`` via ``import_from``.

If SLACK_BOT_TOKEN and SLACK_MANAGER_USER_ID are not set the tool simulates
the Slack send and returns a success response so the workflow completes
end-to-end without real Slack credentials.
"""

from cuga.sdk import CugaAgent
from docs.examples.travel_agent.tools.slack import send_slack_approval

approval_agent = CugaAgent(
    tools=[send_slack_approval],
    # No policies needed — this agent only sends Slack approval requests.
    # Disable auto-loading and filesystem sync so the tool guide policies
    # stored in .cuga/tool_guides/ (intended for compliance_agent only)
    # are not picked up by this agent.
    auto_load_policies=False,
    filesystem_sync=False,
    special_instructions="You handle travel approval workflows. Use hardcoded values: user_name='John Doe', user_email='john@company.com'. Extract all other trip details from the supervisor's message. Save to database and send Slack approval request.",
)
approval_agent.description = (
    "Sends a travel-approval request to the approving manager via Slack. "
    "Falls back to a simulated send if Slack credentials are not configured."
)
