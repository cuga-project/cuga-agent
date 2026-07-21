"""Pure function: translate a slash skill invocation into a planner suggestion.

``/<skill> args`` is a *soft* dispatch: instead of forging conversation turns
that make the planner believe it already ran the skill, we hand the planner a
plain-English input that names the skill and carries the args verbatim. The
planner then decides to call ``load_skill`` itself — the tool is registered in
the CodeAct exec namespace (``cuga_lite/adapter/prepare_node.py``) and skills
are discoverable via the ``<available_skills>`` prompt block, so no extra
plumbing is needed for it to follow the suggestion.

Argument substitution (``$ARGUMENTS`` / ``$N`` / named args from SKILL.md
frontmatter) is applied by ``SkillRegistry.load_skill`` itself when the
planner passes the args through (``skills/registry.py``), so the translation
only needs to carry the raw args text — no substitution happens here.

The user's original utterance (``/<skill> args``) is preserved by the callers
for display and history; only the planner input is translated.

This module is a pure function with no graph, registry, or persistence
dependencies, so the translation can be unit-tested in isolation.
"""

from __future__ import annotations


def translate_skill_invocation(name: str, raw_args: str) -> str:
    """Return the planner input suggesting the named skill.

    ``raw_args`` is embedded verbatim (quotes, newlines and all); an empty
    arg string yields the short form without the trailing ``to:`` clause.
    """
    if raw_args:
        return f"use the skill named '{name}' to: {raw_args}"
    return f"use the skill named '{name}'"
