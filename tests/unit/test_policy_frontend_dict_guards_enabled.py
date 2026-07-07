from __future__ import annotations

from cuga.backend.server.main import _policy_to_frontend_dict


def _tool_guide_policy_dict(**overrides):
    data = {
        "id": "policy_toolguide_flights",
        "name": "Flight booking rules",
        "description": "Rules for booking flights.",
        "type": "tool_guide",
        "enabled": True,
        "triggers": [],
        "priority": 10,
        "target_tools": ["book_flight"],
        "target_apps": ["travel"],
        "guide_content": "Only book refundable flights.",
        "tool_guards": {
            "book_flight": {
                "violating_examples": ["Book a non-refundable flight."],
                "compliance_examples": ["Book a refundable flight."],
                "policy_code": "def guard_tool_call(context):\n    return True\n",
            }
        },
        "prepend": False,
    }
    data.update(overrides)
    return data


def test_policy_to_frontend_dict_preserves_tool_guide_guards_enabled_false() -> None:
    frontend_policy = _policy_to_frontend_dict(_tool_guide_policy_dict(guards_enabled=False))

    assert frontend_policy["policy_type"] == "tool_guide"
    assert frontend_policy["guards_enabled"] is False
    assert frontend_policy["tool_guards"]["book_flight"]["policy_code"]


def test_policy_to_frontend_dict_defaults_tool_guide_guards_enabled_to_true() -> None:
    frontend_policy = _policy_to_frontend_dict(_tool_guide_policy_dict())

    assert frontend_policy["policy_type"] == "tool_guide"
    assert frontend_policy["guards_enabled"] is True
