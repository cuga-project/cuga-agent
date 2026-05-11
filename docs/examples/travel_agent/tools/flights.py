"""
search_flights — searches Google Flights via SerpAPI.

Returns the top 5 results as a JSON string.  Requires SERPAPI_API_KEY in the
environment (or .env file).
"""

import json
import os
from typing import Optional

from langchain_core.tools import tool

try:
    from serpapi import GoogleSearch

    _SERPAPI_AVAILABLE = True
except ImportError:
    _SERPAPI_AVAILABLE = False


@tool
def search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: Optional[str] = None,
    passengers: int = 1,
    travel_class: str = "economy",
) -> str:
    """Search for flights between two airports using Google Flights (via SerpAPI).

    Args:
        origin: IATA airport code for the origin (e.g. 'JFK', 'LAX').
        destination: IATA airport code for the destination.
        departure_date: Departure date in YYYY-MM-DD format.
        return_date: Return date in YYYY-MM-DD format (omit for one-way).
        passengers: Number of passengers (default 1).
        travel_class: One of 'economy', 'premium_economy', 'business', 'first'.

    Returns:
        JSON string with key: flights (list of up to 5 options).
        Each flight has: airline, flight_number, stops, price, travel_class.
    """
    api_key = os.getenv("SERPAPI_API_KEY")
    if not api_key:
        return json.dumps(
            {
                "success": False,
                "error": "SERPAPI_API_KEY is not set. Add it to your .env file.",
            }
        )

    if not _SERPAPI_AVAILABLE:
        return json.dumps(
            {
                "success": False,
                "error": "serpapi package not installed. Run: pip install google-search-results",
            }
        )

    class_map = {"economy": "1", "premium_economy": "2", "business": "3", "first": "4"}
    params = {
        "engine": "google_flights",
        "departure_id": origin.upper(),
        "arrival_id": destination.upper(),
        "outbound_date": departure_date,
        "currency": "USD",
        "hl": "en",
        "api_key": api_key,
        "type": "1" if return_date else "2",
        "adults": passengers,
        "travel_class": class_map.get(travel_class.lower(), "1"),
    }
    if return_date:
        params["return_date"] = return_date

    try:
        results = GoogleSearch(params).get_dict()
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})

    # Surface any API-level error returned by SerpAPI
    if "error" in results:
        return json.dumps({"success": False, "error": results["error"]})

    flights = []
    for section in ("best_flights", "other_flights"):
        for raw in results.get(section, []):
            legs = raw.get("flights", [])
            first_leg = legs[0] if legs else {}
            flights.append(
                {
                    "airline": first_leg.get("airline", "Unknown"),
                    "flight_number": first_leg.get("flight_number", "N/A"),
                    "stops": max(len(legs) - 1, 0),
                    "price": raw.get("price", 0),
                    "travel_class": travel_class,
                }
            )
            if len(flights) >= 5:
                break
        if len(flights) >= 5:
            break

    if not flights:
        # Return the raw top-level keys to help diagnose unexpected response shapes
        available_keys = list(results.keys())
        return json.dumps(
            {
                "success": False,
                "error": (
                    "No flights found. SerpAPI returned no 'best_flights' or "
                    "'other_flights' entries. "
                    f"Top-level response keys: {available_keys}. "
                    "Check that the origin/destination are valid IATA codes and "
                    "the departure_date is in YYYY-MM-DD format."
                ),
            }
        )

    return json.dumps({"flights": flights})
