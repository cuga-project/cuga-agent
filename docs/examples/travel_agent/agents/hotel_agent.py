"""
hotel_agent — searches for available hotels.

Returns the raw JSON string from search_hotels so the compliance agent
can parse and filter the structured data using analyze_travel_compliance.

This module exposes a single ``hotel_agent`` CugaAgent instance used in two ways:

1. **Standalone / SDK** (``main.py``): imported directly.
2. **CUGA UI** (``cuga start travel_agent``): referenced from
   ``config/supervisor_travel_agent.yaml`` via ``import_from``.
"""

from cuga.sdk import CugaAgent
from docs.examples.travel_agent.tools.hotels import search_hotels

hotel_agent = CugaAgent(
    tools=[search_hotels],
    # No policies needed — this agent only searches for hotels.
    # Disable auto-loading and filesystem sync so the tool guide policies
    # stored in .cuga/tool_guides/ (intended for compliance_agent only)
    # are not picked up by this agent.
    auto_load_policies=False,
    filesystem_sync=False,
    special_instructions=(
        "You are a hotel search specialist. "
        "Call search_hotels() with the parameters provided by the supervisor. "
        "Return ONLY the raw JSON string from the tool output — no additional text, "
        "no explanations, no formatting. Your entire response must be the JSON string."
    ),
)
hotel_agent.description = (
    "Searches for available hotels in a city using Google Hotels data. "
    "Returns a raw JSON string with hotel options for use by the compliance agent."
)
