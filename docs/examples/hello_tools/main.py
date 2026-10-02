"""Hello, tools: the smallest CugaAgent with two plain Python tools."""

import asyncio
from datetime import date

from langchain_core.tools import tool

from cuga import CugaAgent

_FACTORS = {("km", "miles"): 0.621371, ("miles", "km"): 1.609344, ("kg", "lb"): 2.204623, ("lb", "kg"): 0.453592}


@tool
def convert_units(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a value between km/miles, kg/lb, or C/F."""
    if (from_unit, to_unit) == ("C", "F"):
        return round(value * 9 / 5 + 32, 2)
    if (from_unit, to_unit) == ("F", "C"):
        return round((value - 32) * 5 / 9, 2)
    if (from_unit, to_unit) not in _FACTORS:
        raise ValueError(f"Unsupported conversion: {from_unit} -> {to_unit}")
    return round(value * _FACTORS[(from_unit, to_unit)], 2)


@tool
def days_between(start_date: str, end_date: str) -> int:
    """Number of days from start_date to end_date (both YYYY-MM-DD)."""
    return (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days


async def main() -> None:
    agent = CugaAgent(tools=[convert_units, days_between])
    result = await agent.invoke("How many miles is 42 km, and how many days from 2026-01-01 to 2026-03-15?")
    print(result.answer)


if __name__ == "__main__":
    asyncio.run(main())
