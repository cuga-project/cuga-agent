"""
search_hotels — searches Google Hotels via SerpAPI.

Returns the top 5 results as a JSON string.  Requires SERPAPI_API_KEY in the
environment (or .env file).
"""

import json
import os

from langchain_core.tools import tool

try:
    from serpapi import GoogleSearch

    _SERPAPI_AVAILABLE = True
except ImportError:
    _SERPAPI_AVAILABLE = False


@tool
def search_hotels(
    location: str,
    check_in_date: str,
    check_out_date: str,
    guests: int = 1,
) -> str:
    """Search for hotels in a city using Google Hotels (via SerpAPI).

    Args:
        location: City or area name (e.g. 'Los Angeles, CA').
        check_in_date: Check-in date in YYYY-MM-DD format.
        check_out_date: Check-out date in YYYY-MM-DD format.
        guests: Number of guests (default 1).

    Returns:
        JSON string with key: hotels (list of up to 7 options).
        Each hotel has: name, stars, price_per_night, amenities.
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

    params = {
        "engine": "google_hotels",
        "q": f"Hotels in {location}",
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "adults": guests,
        "currency": "USD",
        "gl": "us",
        "hl": "en",
        "api_key": api_key,
    }

    try:
        results = GoogleSearch(params).get_dict()
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)})

    hotels = []
    for raw in results.get("properties", [])[:7]:
        # SerpAPI returns price as a string like "$220" — strip the symbol
        raw_rate = raw.get("rate_per_night", {}).get("lowest", "0")
        try:
            price_per_night = float(str(raw_rate).replace("$", "").replace(",", ""))
        except ValueError:
            price_per_night = 0.0

        hotels.append(
            {
                "name": raw.get("name", "Unknown Hotel"),
                "stars": raw.get("hotel_class", 0),
                "price_per_night": price_per_night,
                "amenities": raw.get("amenities", [])[:7],
            }
        )

    return json.dumps({"hotels": hotels})
