"""Unit tests for is_todo_only_script — the guard that skips the reflection pass
after a code block that only updates todos (issue #676).

A todo-only block changes no application state, so the reflection generation that
normally follows it can only restate "nothing happened". Getting this predicate
wrong in either direction is costly: too loose and we skip reflection on a block
that did real work; too strict and the no-op reflections stay.
"""

from __future__ import annotations

import pytest

from cuga.backend.cuga_graph.nodes.cuga_agent_core.execution.todos import is_todo_only_script


@pytest.mark.unit
@pytest.mark.parametrize(
    "script",
    [
        # canonical form the prompt asks for
        'todos = await create_update_todos([{"text": "a", "status": "pending"}])\nprint(todos)',
        # dict payload, multi-line call
        (
            "todos = await create_update_todos({\n"
            '    "todos": [\n'
            '        {"text": "Fetch cart", "status": "pending"},\n'
            '        {"text": "Filter items", "status": "pending"},\n'
            "    ]\n"
            "})\n"
            "print(todos)"
        ),
        # non-tool calls around it must not count
        (
            "import json\n"
            "# refresh the plan\n"
            'todos = await create_update_todos([{"text": "a", "status": "completed"}])\n'
            "print(json.dumps(len(todos.todos)))"
        ),
        # duplicated fences joined into one script (observed with gpt-oss-120b)
        (
            'await create_update_todos([{"text": "a", "status": "pending"}])\n'
            'await create_update_todos([{"text": "a", "status": "pending"}])'
        ),
    ],
)
def test_todo_only_blocks_are_detected(script):
    assert is_todo_only_script(script) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "script",
    [
        # todo call mixed with real work — reflection must still run
        (
            'await create_update_todos([{"text": "a", "status": "completed"}])\n'
            "cart = await amazon_show_cart_cart_get()\n"
            "print(cart)"
        ),
        # real work only
        "cart = await amazon_show_cart_cart_get()\nprint(cart)",
        # find_tools is a different isolated tool, not a todo update
        'tools = await find_tools("list cart", "amazon")\nprint(tools)',
        # no awaited call at all
        "print('hello')",
        "",
        # attribute-style tool call alongside the todo update
        (
            'await create_update_todos([{"text": "a", "status": "pending"}])\n'
            "await client.amazon_show_cart_cart_get()"
        ),
    ],
)
def test_non_todo_only_blocks_are_not_detected(script):
    assert is_todo_only_script(script) is False


@pytest.mark.unit
def test_unparsable_script_falls_back_to_regex():
    """The model sometimes emits code that does not parse; the guard must still
    classify it rather than raise."""
    broken = 'todos = await create_update_todos([{"text": "a", "status": "pending"}]\nprint(todos'
    assert is_todo_only_script(broken) is True

    broken_mixed = 'await create_update_todos([{"text": "a"}]\ncart = await amazon_show_cart_cart_get(\n'
    assert is_todo_only_script(broken_mixed) is False


@pytest.mark.unit
def test_none_script_is_safe():
    assert is_todo_only_script(None) is False
