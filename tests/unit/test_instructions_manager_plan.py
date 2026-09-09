import pytest

from cuga.configurations.instructions_manager import InstructionsManager

pytestmark = pytest.mark.unit


@pytest.fixture
def instructions_manager():
    manager = InstructionsManager()
    original_cache = dict(manager._in_memory_cache)
    try:
        manager._in_memory_cache.clear()
        yield manager
    finally:
        manager._in_memory_cache.clear()
        manager._in_memory_cache.update(original_cache)


def test_structured_plan_reaches_formatted_agent_instructions(instructions_manager):
    instructions_manager.set_instructions_from_one_file(
        "## Plan\nUse the filesystem tool.\n\n## Answer\nKeep the response concise."
    )

    formatted = instructions_manager.get_all_instructions_formatted()

    assert formatted is not None
    assert "**Plan**" in formatted
    assert "Use the filesystem tool." in formatted
    assert "**Answer**" in formatted
    assert "Keep the response concise." in formatted


def test_unsectioned_instructions_are_preserved_as_plan(instructions_manager):
    instructions_manager.set_instructions_from_one_file("Use only approved tools.")

    formatted = instructions_manager.get_all_instructions_formatted()

    assert formatted is not None
    assert "**Plan**" in formatted
    assert "Use only approved tools." in formatted
