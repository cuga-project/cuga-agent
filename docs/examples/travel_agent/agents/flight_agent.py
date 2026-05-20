"""
flight_agent — searches for available flights.

Returns the raw JSON string from search_flights so the compliance agent
can parse and filter the structured data using analyze_travel_compliance.

This module exposes a single ``flight_agent`` CugaAgent instance used in two ways:

1. **Standalone / SDK** (``main.py``): imported directly.
2. **CUGA UI** (``cuga start travel_agent``): referenced from
   ``config/supervisor_travel_agent.yaml`` via ``import_from``.
"""

from cuga.sdk import CugaAgent
from docs.examples.travel_agent.tools.flights import search_flights

flight_agent = CugaAgent(
    tools=[search_flights],
    # No policies needed — this agent only searches for flights.
    # Disable auto-loading and filesystem sync so the tool guide policies
    # stored in .cuga/tool_guides/ (intended for compliance_agent only)
    # are not picked up by this agent.
    auto_load_policies=False,
    filesystem_sync=False,
    special_instructions=(
        "You are a flight search specialist. "
        "Call search_flights() with the parameters provided by the supervisor. "
        "Return ONLY the raw JSON string from the tool output — no additional text, "
        "no explanations, no formatting. Your entire response must be the JSON string."
    ),
)
flight_agent.description = (
    "Searches for available flights between two airports using Google Flights data. "
    "Returns a raw JSON string with flight options for use by the compliance agent."
)
