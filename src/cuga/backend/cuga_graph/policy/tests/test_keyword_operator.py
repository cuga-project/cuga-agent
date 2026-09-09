"""Tests for keyword trigger operator (AND/OR) functionality."""

import pytest

from cuga.backend.cuga_graph.policy.models import IntentGuard, IntentGuardResponse, KeywordTrigger
from cuga.backend.cuga_graph.policy.agent import PolicyAgent, PolicyContext
from cuga.backend.cuga_graph.policy.storage import PolicyStorage


@pytest.mark.asyncio
async def test_keyword_trigger_and_operator():
    """Test that AND operator requires all keywords to match."""
    print("\n" + "=" * 80)
    print("TEST: Keyword Trigger AND Operator")
    print("=" * 80)

    # Create policy with AND operator (default)
    policy = IntentGuard(
        id="test_and",
        name="AND Test Policy",
        description="Test AND operator",
        triggers=[
            KeywordTrigger(
                value=["urgent", "payment"],
                target="intent",
                case_sensitive=False,
                operator="and",
            )
        ],
        response=IntentGuardResponse(
            response_type="natural_language",
            content="Blocked by AND policy",
        ),
        allow_override=False,
        priority=50,
        enabled=True,
    )

    # Create minimal storage (not used for these tests, but required by PolicyAgent)
    storage = PolicyStorage(collection_name="test_keyword_operator")
    await storage.initialize_async()
    agent = PolicyAgent(storage=storage)

    # Test 1: Both keywords present - should match
    print("\n📋 Test 1: Both keywords present")
    print("-" * 80)
    context1 = PolicyContext(
        user_input="I have an urgent payment issue",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched1, confidence1, trigger_details1 = await agent._check_trigger(policy.triggers[0], context1)
    print(f"  Input: '{context1.user_input}'")
    print(f"  Matched: {matched1}")
    print(f"  Confidence: {confidence1}")
    assert matched1, "Should match when both keywords present with AND"
    assert confidence1 == 1.0, "Confidence should be 1.0 when all keywords match with AND"
    print(f"  ✅ Matched with confidence: {confidence1}")

    # Test 2: Only one keyword present - should NOT match
    print("\n📋 Test 2: Only one keyword present")
    print("-" * 80)
    context2 = PolicyContext(
        user_input="This is urgent",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched2, confidence2, trigger_details2 = await agent._check_trigger(policy.triggers[0], context2)
    print(f"  Input: '{context2.user_input}'")
    print(f"  Matched: {matched2}")
    assert not matched2, "Should NOT match when only one keyword present with AND"
    print("  ✅ Correctly did not match (AND requires all keywords)")

    # Test 3: No keywords present - should NOT match
    print("\n📋 Test 3: No keywords present")
    print("-" * 80)
    context3 = PolicyContext(
        user_input="I need help with something",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched3, confidence3, trigger_details3 = await agent._check_trigger(policy.triggers[0], context3)
    print(f"  Input: '{context3.user_input}'")
    print(f"  Matched: {matched3}")
    assert not matched3, "Should NOT match when no keywords present"
    print("  ✅ Correctly did not match")

    print("\n" + "=" * 80)
    print("✅ AND Operator Test Passed")
    print("=" * 80)


@pytest.mark.asyncio
async def test_keyword_trigger_or_operator():
    """Test that OR operator requires any keyword to match."""
    print("\n" + "=" * 80)
    print("TEST: Keyword Trigger OR Operator")
    print("=" * 80)

    # Create policy with OR operator
    policy = IntentGuard(
        id="test_or",
        name="OR Test Policy",
        description="Test OR operator",
        triggers=[
            KeywordTrigger(
                value=["help", "support", "assist"],
                target="intent",
                case_sensitive=False,
                operator="or",
            )
        ],
        response=IntentGuardResponse(
            response_type="natural_language",
            content="Blocked by OR policy",
        ),
        allow_override=False,
        priority=50,
        enabled=True,
    )

    # Create minimal storage (not used for these tests, but required by PolicyAgent)
    storage = PolicyStorage(collection_name="test_keyword_operator")
    await storage.initialize_async()
    agent = PolicyAgent(storage=storage)

    # Test 1: One keyword present - should match
    print("\n📋 Test 1: One keyword present ('help')")
    print("-" * 80)
    context1 = PolicyContext(
        user_input="I need help with this",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched1, confidence1, reason1 = await agent._check_trigger(policy.triggers[0], context1)
    print(f"  Input: '{context1.user_input}'")
    print(f"  Matched: {matched1}")
    print(f"  Confidence: {confidence1}")
    print(f"  Reason: {reason1}")
    assert matched1, "Should match when any keyword present with OR"
    print(f"  ✅ Matched with confidence: {confidence1}")

    # Test 2: Different keyword present - should match
    print("\n📋 Test 2: Different keyword present ('support')")
    print("-" * 80)
    context2 = PolicyContext(
        user_input="Contact support please",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched2, confidence2, reason2 = await agent._check_trigger(policy.triggers[0], context2)
    print(f"  Input: '{context2.user_input}'")
    print(f"  Matched: {matched2}")
    print(f"  Confidence: {confidence2}")
    print(f"  Reason: {reason2}")
    assert matched2, "Should match when any keyword present with OR"
    print(f"  ✅ Matched with confidence: {confidence2}")

    # Test 3: Multiple keywords present - should match
    print("\n📋 Test 3: Multiple keywords present ('help' and 'assist')")
    print("-" * 80)
    context3 = PolicyContext(
        user_input="Can you help and assist me?",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched3, confidence3, reason3 = await agent._check_trigger(policy.triggers[0], context3)
    print(f"  Input: '{context3.user_input}'")
    print(f"  Matched: {matched3}")
    print(f"  Confidence: {confidence3}")
    print(f"  Reason: {reason3}")
    assert matched3, "Should match when multiple keywords present with OR"
    print(f"  ✅ Matched with confidence: {confidence3}")

    # Test 4: No keywords present - should NOT match
    print("\n📋 Test 4: No keywords present")
    print("-" * 80)
    context4 = PolicyContext(
        user_input="I want to buy something",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched4, confidence4, reason4 = await agent._check_trigger(policy.triggers[0], context4)
    print(f"  Input: '{context4.user_input}'")
    print(f"  Matched: {matched4}")
    assert not matched4, "Should NOT match when no keywords present"
    print("  ✅ Correctly did not match")

    print("\n" + "=" * 80)
    print("✅ OR Operator Test Passed")
    print("=" * 80)


@pytest.mark.asyncio
async def test_keyword_operator_case_sensitivity():
    """Test that operators work with case sensitivity settings."""
    print("\n" + "=" * 80)
    print("TEST: Keyword Operator with Case Sensitivity")
    print("=" * 80)

    # Create policy with OR operator and case sensitivity
    policy = IntentGuard(
        id="test_case",
        name="Case Sensitive OR Test",
        description="Test OR operator with case sensitivity",
        triggers=[
            KeywordTrigger(
                value=["URGENT", "CRITICAL"],
                target="intent",
                case_sensitive=True,
                operator="or",
            )
        ],
        response=IntentGuardResponse(
            response_type="natural_language",
            content="Blocked by case-sensitive policy",
        ),
        allow_override=False,
        priority=50,
        enabled=True,
    )

    # Create minimal storage (not used for these tests, but required by PolicyAgent)
    storage = PolicyStorage(collection_name="test_keyword_operator")
    await storage.initialize_async()
    agent = PolicyAgent(storage=storage)

    # Test 1: Correct case - should match
    print("\n📋 Test 1: Correct case (URGENT)")
    print("-" * 80)
    context1 = PolicyContext(
        user_input="This is URGENT",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched1, confidence1, trigger_details1 = await agent._check_trigger(policy.triggers[0], context1)
    print(f"  Input: '{context1.user_input}'")
    print(f"  Matched: {matched1}")
    assert matched1, "Should match with correct case"
    print(f"  ✅ Matched with confidence: {confidence1}")

    # Test 2: Wrong case - should NOT match
    print("\n📋 Test 2: Wrong case (urgent)")
    print("-" * 80)
    context2 = PolicyContext(
        user_input="This is urgent",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )
    matched2, confidence2, trigger_details2 = await agent._check_trigger(policy.triggers[0], context2)
    print(f"  Input: '{context2.user_input}'")
    print(f"  Matched: {matched2}")
    assert not matched2, "Should NOT match with wrong case when case_sensitive=True"
    print("  ✅ Correctly did not match (case mismatch)")

    print("\n" + "=" * 80)
    print("✅ Case Sensitivity Test Passed")
    print("=" * 80)


@pytest.mark.asyncio
async def test_keyword_trigger_empty_keywords_does_not_match():
    """Test that a KeywordTrigger with empty keywords never matches (issue #766)."""
    policy_and = IntentGuard(
        id="test_empty_and",
        name="Empty AND Policy",
        description="Test empty keywords with AND",
        triggers=[
            KeywordTrigger(
                value=[],
                target="intent",
                case_sensitive=False,
                operator="and",
            )
        ],
        response=IntentGuardResponse(
            response_type="natural_language",
            content="Blocked",
        ),
        enabled=True,
    )
    policy_or = IntentGuard(
        id="test_empty_or",
        name="Empty OR Policy",
        description="Test empty keywords with OR",
        triggers=[
            KeywordTrigger(
                value=[],
                target="intent",
                case_sensitive=False,
                operator="or",
            )
        ],
        response=IntentGuardResponse(
            response_type="natural_language",
            content="Blocked",
        ),
        enabled=True,
    )

    storage = PolicyStorage(collection_name="test_keyword_empty")
    await storage.initialize_async()
    agent = PolicyAgent(storage=storage)

    context = PolicyContext(
        user_input="Hello world, any arbitrary message",
        chat_messages=[],
        sub_task="",
        agent_response="",
    )

    matched_and, conf_and, _ = await agent._check_trigger(policy_and.triggers[0], context)
    assert not matched_and
    assert conf_and == 0.0

    matched_or, conf_or, _ = await agent._check_trigger(policy_or.triggers[0], context)
    assert not matched_or
    assert conf_or == 0.0


if __name__ == "__main__":
    import asyncio

    asyncio.run(test_keyword_trigger_and_operator())
    asyncio.run(test_keyword_trigger_or_operator())
    asyncio.run(test_keyword_operator_case_sensitivity())
