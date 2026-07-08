from __future__ import annotations

import os
from functools import lru_cache

from cuga.sdk import CugaAgent

from docs.examples.suggesthub.tools.suggesthub_tools import SUGGESTHUB_TOOLS

_SKILL_PATH = os.path.join(os.path.dirname(__file__), "SKILL.md")
IAN_INSTRUCTIONS = open(_SKILL_PATH, encoding="utf-8").read()


@lru_cache(maxsize=1)
def get_bob_agent() -> CugaAgent:
    agent = CugaAgent(
        tools=SUGGESTHUB_TOOLS,
        auto_load_policies=False,
        filesystem_sync=False,
        special_instructions=IAN_INSTRUCTIONS,
    )
    agent.description = "IBM SuggestHub intake agent for dedupe, drafting, publishing, and manager support."
    return agent
