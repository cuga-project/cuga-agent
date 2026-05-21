"""
Travel Agent — standalone SDK example
======================================

Demonstrates a multi-agent corporate travel planning workflow using
CugaSupervisor.  Run this script directly for a command-line experience, or
use `cuga start travel_agent` to get the full chat UI.

Usage
-----
    python main.py

Prerequisites
-------------
    OPENAI_API_KEY   — required (or configure another LLM provider)
    SERPAPI_API_KEY  — required for real flight/hotel data
    SLACK_BOT_TOKEN  — optional (approval is simulated if not set)
    SLACK_MANAGER_USER_ID — optional (required together with SLACK_BOT_TOKEN)

Copy .env.example to .env and fill in your keys before running.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from cuga import CugaSupervisor

# Load .env from the same directory as this file
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv

    load_dotenv(_env_file)

# Path to the YAML config (single source of truth for agents + special_instructions)
_yaml_config = Path(__file__).parent / "config" / "supervisor_travel_agent.yaml"


def get_next_week_dates() -> tuple[str, str]:
    """
    Calculate next week's Monday and Friday dates.

    Returns:
        tuple[str, str]: (start_date, end_date) in YYYY-MM-DD format
        (the format required by SerpAPI / search_flights).
    """
    today = datetime.now()

    # Calculate days until next Monday (0 = Monday, 6 = Sunday)
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:  # If today is Monday, get next Monday
        days_until_monday = 7

    # Get next Monday
    next_monday = today + timedelta(days=days_until_monday)

    # Get Friday of that week (4 days after Monday)
    next_friday = next_monday + timedelta(days=4)

    # Format as YYYY-MM-DD (required by SerpAPI)
    start_date = next_monday.strftime("%Y-%m-%d")
    end_date = next_friday.strftime("%Y-%m-%d")

    return start_date, end_date


async def main() -> None:
    print("\n" + "=" * 60)
    print("✈️  Corporate Travel Agent")
    print("=" * 60)

    # Load supervisor from YAML — same config used by `cuga start travel_agent`
    # This ensures the special_instructions (workflow guidance) are applied.
    supervisor = await CugaSupervisor.from_yaml(str(_yaml_config))

    thread_id = "travel-demo-001"

    # -----------------------------------------------------------------------
    # Turn 1: Request a trip
    # -----------------------------------------------------------------------
    # Calculate next week's Monday-Friday dates
    start_date, end_date = get_next_week_dates()
    trip_message = f"plan a trip from ny to boston, from {start_date} to {end_date}"

    print(f"\n👤 User: {trip_message}\n")
    result = await supervisor.invoke(
        message=trip_message,
        thread_id=thread_id,
    )
    print(f"🤖 Supervisor:\n{result.answer}\n")

    if result.error:
        print(f"❌ Error: {result.error}")
        return

    # -----------------------------------------------------------------------
    # Turn 2: Select flight and hotel
    # -----------------------------------------------------------------------
    # Include the full trip context (origin, destination, dates) and the
    # compliance results from Turn 1 so the approval_agent has everything it
    # needs without asking follow-up questions.
    selection_message = (
        f"Trip details: {trip_message}\n\n"
        f"Compliance results from previous step:\n{result.answer}\n\n"
        "User selection: I'll take the first compliant flight and the first compliant hotel. "
        "Please send the approval request now."
    )
    print("\n👤 User: I'll take the first compliant flight and the first compliant hotel.\n")
    result = await supervisor.invoke(
        selection_message,
        thread_id=thread_id,
    )
    print(f"🤖 Supervisor:\n{result.answer}\n")

    if result.error:
        print(f"❌ Error: {result.error}")


if __name__ == "__main__":
    asyncio.run(main())
