"""
send_slack_approval — sends a travel-approval request to a manager via Slack.

If SLACK_BOT_TOKEN and SLACK_MANAGER_USER_ID are set the message is sent for
real using the Slack Web API.  Otherwise the tool logs the message and returns
a simulated success response so the example still runs end-to-end without
Slack credentials.
"""

import json
import os
from datetime import datetime, timezone

from langchain_core.tools import tool

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError, SlackClientError, SlackRequestError

    _SLACK_SDK_AVAILABLE = True
except ImportError:
    _SLACK_SDK_AVAILABLE = False


@tool
def send_slack_approval(
    employee_name: str,
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str,
    flight_summary: str,
    hotel_summary: str,
    total_cost: float,
) -> str:
    """Send a travel-approval request to the approving manager via Slack.

    If Slack is not configured (SLACK_BOT_TOKEN / SLACK_MANAGER_USER_ID not set)
    the tool simulates the send and returns a success response so the workflow
    can still complete end-to-end.

    Args:
        employee_name: Full name of the employee requesting approval.
        origin: Trip origin (city or airport code).
        destination: Trip destination (city or airport code).
        departure_date: Departure date (YYYY-MM-DD or human-readable).
        return_date: Return date (YYYY-MM-DD or human-readable).
        flight_summary: One-line description of the selected flight.
        hotel_summary: One-line description of the selected hotel.
        total_cost: Estimated total trip cost in USD.

    Returns:
        JSON string with keys: success, mode ('slack' or 'simulated'), message, timestamp.
    """
    token = os.getenv("SLACK_BOT_TOKEN", "")
    manager_id = os.getenv("SLACK_MANAGER_USER_ID", "")
    timestamp = datetime.now(timezone.utc).isoformat()

    message_text = (
        f"✈️ *Travel Approval Request*\n\n"
        f"*Employee:* {employee_name}\n"
        f"*Route:* {origin} → {destination}\n"
        f"*Dates:* {departure_date} – {return_date}\n"
        f"*Flight:* {flight_summary}\n"
        f"*Hotel:* {hotel_summary}\n"
        f"*Estimated Total:* ${total_cost:,.2f}\n\n"
        f"Please reply ✅ to approve or ❌ to reject."
    )

    # --- Real Slack send ---
    if _SLACK_SDK_AVAILABLE and token and token != "xoxb-your-slack-bot-token-here" and manager_id:
        try:
            client = WebClient(token=token)
            response = client.chat_postMessage(
                channel=manager_id,
                text=message_text,
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": message_text},
                    }
                ],
            )
            return json.dumps(
                {
                    "success": True,
                    "mode": "slack",
                    "message": "Approval request sent to manager via Slack.",
                    "channel": response.get("channel"),
                    "ts": response.get("ts"),
                    "timestamp": timestamp,
                },
                indent=2,
            )
        except (SlackApiError, SlackClientError, SlackRequestError) as exc:
            return json.dumps(
                {
                    "success": False,
                    "mode": "slack",
                    "error": str(exc),
                    "timestamp": timestamp,
                },
                indent=2,
            )
        except Exception as exc:
            return json.dumps(
                {
                    "success": False,
                    "mode": "slack",
                    "error": str(exc),
                    "timestamp": timestamp,
                },
                indent=2,
            )

    # --- Simulated send (no Slack credentials) ---
    import logging

    logging.getLogger(__name__).info("[SIMULATED SLACK] Approval request simulated (details omitted).")
    return json.dumps(
        {
            "success": True,
            "mode": "simulated",
            "message": (
                "Slack not configured — approval request simulated. "
                "Set SLACK_BOT_TOKEN and SLACK_MANAGER_USER_ID to send real messages."
            ),
            "approval_details": {
                "employee": employee_name,
                "route": f"{origin} → {destination}",
                "dates": f"{departure_date} – {return_date}",
                "flight": flight_summary,
                "hotel": hotel_summary,
                "total_cost_usd": total_cost,
            },
            "timestamp": timestamp,
        },
        indent=2,
    )
