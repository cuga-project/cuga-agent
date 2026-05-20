"""
analyze_travel_compliance — compliance tool for the travel agent.

This tool is a "smart placeholder" — it receives flight and hotel options
and returns a prompt for the LLM to reason about. The actual policy
enforcement is done by the LLM, guided by the Tool Guide policy injected
into this tool's context by the CUGA policy system.

The Tool Guide (`.cuga/tool_guides/policy_*.md`) injects role-based
policy constraints (max prices, allowed classes, budget limits) directly
into this tool's description when the compliance agent calls it.
"""

from langchain_core.tools import tool

# Allowed roles for travel compliance analysis
ALLOWED_ROLES = ["employee", "manager", "executive"]


@tool
def analyze_travel_compliance(
    flights_json: str,
    hotels_json: str,
    role: str = "employee",
) -> str:
    """Analyze travel options for policy compliance using LLM reasoning.

    This tool receives flight and hotel options and applies role-based
    company travel policies to filter and validate them. The Tool Guide
    system injects detailed policy constraints into this tool's context.

    Args:
        flights_json: JSON string with flights list. Each flight has:
            airline, flight_number, stops, price, travel_class.
            Example: '{"flights": [{"airline": "United", "flight_number": "UA123",
            "stops": 0, "price": 450, "travel_class": "economy"}]}'
        hotels_json: JSON string with hotels list. Each hotel has:
            name, stars, price_per_night, amenities.
            Example: '{"hotels": [{"name": "Budget Inn", "stars": 3,
            "price_per_night": 120, "amenities": ["Free Wi-Fi"]}]}'
        role: User role — 'employee', 'manager', or 'executive' (default: 'employee').

    Returns:
        Analysis of compliant options with explanations in markdown format.

    Note:
        This tool uses LLM reasoning guided by the CUGA Tool Guide policy system.
        The Tool Guide injects role-specific policy constraints (max prices,
        allowed classes, budget limits) directly into this tool's context.
    """
    # Validate role against allowlist
    if role not in ALLOWED_ROLES:
        raise ValueError(f"Invalid role '{role}'. Supported roles are: {', '.join(ALLOWED_ROLES)}")

    # This is a placeholder — the actual filtering is done by the LLM
    # with policy guidance injected by the Tool Guide system.
    return (
        f"Analyzing travel options for role: {role}\n\n"
        f"Flights to evaluate:\n{flights_json}\n\n"
        f"Hotels to evaluate:\n{hotels_json}\n\n"
        "Apply the role-based policy constraints from your instructions to filter "
        "these options. Return compliant options in two markdown tables "
        "(one for flights, one for hotels) with rejection explanations."
    )
