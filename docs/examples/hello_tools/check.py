"""Keyless smoke check: tools return the right values and CugaAgent accepts them (no LLM call)."""

import asyncio

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from cuga import CugaAgent
from main import convert_units, days_between

assert convert_units.invoke({"value": 42, "from_unit": "km", "to_unit": "miles"}) == 26.1
assert convert_units.invoke({"value": 100, "from_unit": "C", "to_unit": "F"}) == 212.0
assert days_between.invoke({"start_date": "2026-01-01", "end_date": "2026-03-15"}) == 73

agent = CugaAgent(tools=[convert_units, days_between], model=FakeListChatModel(responses=["unused"]))
asyncio.run(agent.tool_provider.initialize())
names = [t.name for t in asyncio.run(agent.tool_provider.get_all_tools())]
assert names == ["convert_units", "days_between"], names
print("OK:", names)
