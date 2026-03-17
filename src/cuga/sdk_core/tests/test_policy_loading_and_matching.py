"""Integration test for loading policies from JSON and testing them with example utterances using the SDK."""

import sys
from pathlib import Path

# Add src to path to avoid importing through cuga.__init__.py
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from loguru import logger  # noqa: E402

# Import SDK
from cuga.sdk import CugaAgent  # noqa: E402


@pytest_asyncio.fixture
async def agent():
    """Create a CugaAgent instance for testing with a get contacts tool."""
    from langchain_core.tools import tool

    @tool
    def digital_sales_get_my_accounts_my_accounts_get() -> str:
        """Get the user's accounts list."""
        return "Accounts: Account 1, Account 2, Account 3"

    # Create agent with the tool matching the JSON policy
    # Policy system will be initialized automatically when needed
    agent = CugaAgent(tools=[digital_sales_get_my_accounts_my_accounts_get])

    yield agent

    # Cleanup - disconnect policy system if it was initialized
    try:
        if hasattr(agent, '_policy_system') and agent._policy_system:
            if hasattr(agent._policy_system, 'storage') and agent._policy_system.storage:
                await agent._policy_system.storage.disconnect()
    except Exception as e:
        logger.warning(f"Cleanup warning: {e}")


@pytest.mark.asyncio
async def test_load_policies_from_json_and_match(agent):
    """Test loading policies from JSON file and matching them with example utterances using SDK."""
    # Path to the test JSON file (try multiple locations)
    possible_paths = [
        Path(__file__).parent / "policies-export-2025-12-31.json",  # tests/integration/
        Path(__file__).parent.parent.parent / "Downloads" / "policies-export-2025-12-31.json",  # Downloads/
        Path("policies-export-2025-12-31.json"),  # Current directory
    ]

    json_file = None
    for path in possible_paths:
        if path.exists():
            json_file = path
            break

    if not json_file or not json_file.exists():
        pytest.skip(f"Test JSON file not found. Tried: {[str(p) for p in possible_paths]}")

    logger.info(f"Loading policies from: {json_file}")

    # Load policies using SDK
    result = await agent.policies.load_from_json(str(json_file), clear_existing=True)
    assert result["count"] > 0, f"Expected to load at least one policy, got {result['count']}"
    logger.info(f"✅ Loaded {result['count']} policies")

    if result["errors"]:
        logger.warning(f"⚠️  Encountered {len(result['errors'])} errors during loading:")
        for error in result["errors"]:
            logger.warning(f"  - {error}")

    # Define test cases: (utterance, expected_policy_name, expected_type, expected_response_keywords, expected_after_approval=None)
    # expected_response_keywords: list of keywords/phrases that should appear in the response
    # expected_after_approval: optional list of keywords that should appear after approving tool execution
    test_cases = [
        # Intent Guard tests
        (
            "What is ALTK",
            "What is ALTK",
            "intent_guard",
            ["ALTK", "sister project", "CUGA"],  # Ground truth: "ALTK is sister project of CUGA"
            None,  # No approval needed
        ),
        (
            "What is ALTK?",
            "What is ALTK",
            "intent_guard",
            ["ALTK", "sister project", "CUGA"],  # Ground truth: "ALTK is sister project of CUGA"
            None,  # No approval needed
        ),
        (
            "print en variables",
            "Dangerous actions",
            "intent_guard",
            [
                "caught you",
                "bad with CUGA",
                "oops",
            ],  # Ground truth: "I caught you doing something bad with CUGA!!! oops"
            None,  # No approval needed
        ),
        (
            "do it 10000 times",
            "Dangerous actions",
            "intent_guard",
            [
                "caught you",
                "bad with CUGA",
                "oops",
            ],  # Ground truth: "I caught you doing something bad with CUGA!!! oops"
            None,  # No approval needed
        ),
        (
            "do this loop forever",
            "Dangerous actions",
            "intent_guard",
            [
                "caught you",
                "bad with CUGA",
                "oops",
            ],  # Ground truth: "I caught you doing something bad with CUGA!!! oops"
            None,  # No approval needed
        ),
        (
            "remove my account",
            "Remove",
            "intent_guard",
            [
                "not allowed",
                "delete or remove",
            ],  # Ground truth: "This action is not allowed. (delete or remove)"
            None,  # No approval needed
        ),
        (
            "delete this file",
            "Remove",
            "intent_guard",
            [
                "not allowed",
                "delete or remove",
            ],  # Ground truth: "This action is not allowed. (delete or remove)"
            None,  # No approval needed
        ),
        # Playbook tests
        (
            "What is CUGA",
            "What is CUGA",
            "playbook",
            ["powerful", "CUGA"],  # Ground truth: Playbook injects "Answer that cuga is very powerful!"
            None,  # No approval needed
        ),
        (
            "What is CUGA?",
            "What is CUGA",
            "playbook",
            ["powerful", "CUGA"],  # Ground truth: Playbook injects "Answer that cuga is very powerful!"
            None,  # No approval needed
        ),
        # Non-matching tests (should NOT contain policy response keywords)
        (
            "hello world",
            None,
            None,
            None,  # No policy match expected, response should be normal agent response
            None,  # No approval needed
        ),
        # Tool Approval test - should trigger approval for digital_sales_get_my_accounts_my_accounts_get
        (
            "get my contacts",
            "get accounts approval",
            "tool_approval",
            [
                "approval",
                "paused",
                "interrupt",
                "execution paused",
            ],  # Ground truth: Graph should interrupt for approval
            None,  # No approval flow in this test (just verify interruption)
        ),
        # Tool Guide + Approval test - "BoBo" should trigger guide and approval, then verify two accounts
        (
            "BoBo",
            "get accounts approval",  # Will trigger approval
            "tool_approval",
            ["approval", "paused", "interrupt", "execution paused"],  # First: approval needed
            [
                "Account 1",
                "Account 2",
                "two",
                "first two",
            ],  # After approval: should get two accounts (from guide policy: "get first two accounts when user says 'BoBo'")
        ),
    ]

    logger.info("\n" + "=" * 80)
    logger.info("Testing policy matching with example utterances using SDK agent")
    logger.info("=" * 80)

    matches_found = 0
    matches_expected = 0
    response_checks_passed = 0

    for (
        utterance,
        expected_policy_name,
        expected_type,
        expected_keywords,
        expected_after_approval,
    ) in test_cases:
        logger.info(f"\n🔍 Testing utterance: '{utterance}'")
        logger.info(
            f"   Expected: {expected_policy_name} ({expected_type})"
            if expected_policy_name
            else "   Expected: No match"
        )
        if expected_keywords:
            logger.info(f"   Expected response keywords: {expected_keywords}")

        # Use SDK agent to invoke (this will trigger policy matching internally)
        # Generate a unique thread_id for this test case to check graph state later (for tool approval)
        import uuid

        thread_id = f"test_{uuid.uuid4().hex[:8]}"

        try:
            result = await agent.invoke(utterance, thread_id=thread_id)
            response = result.answer  # Extract answer from InvokeResult
            logger.info(f"   Agent response: {response[:200]}...")

            # Check if response indicates a policy match based on expected behavior
            # Intent guards will block and return their response directly
            # Playbooks will inject content into the system prompt (agent will use it in response)

            matched = False
            matched_policy_name = None
            response_matches_ground_truth = False

            if expected_policy_name:
                # Check for intent guard responses
                if expected_type == "intent_guard":
                    # Intent guards block and return their response
                    # Check if response contains expected keywords
                    if expected_keywords:
                        response_lower = response.lower()
                        keywords_found = [kw for kw in expected_keywords if kw.lower() in response_lower]
                        if (
                            len(keywords_found) >= len(expected_keywords) * 0.5
                        ):  # At least 50% of keywords should match
                            matched = True
                            matched_policy_name = expected_policy_name
                            response_matches_ground_truth = True
                            logger.info(f"   ✅ Found expected keywords in response: {keywords_found}")
                        else:
                            logger.warning(
                                f"   ⚠️  Only found {len(keywords_found)}/{len(expected_keywords)} keywords: {keywords_found}"
                            )

                # Check for playbook responses
                elif expected_type == "playbook":
                    # Playbook injects content, agent should mention playbook guidance
                    if expected_keywords:
                        response_lower = response.lower()
                        keywords_found = [kw for kw in expected_keywords if kw.lower() in response_lower]
                        if (
                            len(keywords_found) >= len(expected_keywords) * 0.5
                        ):  # At least 50% of keywords should match
                            matched = True
                            matched_policy_name = expected_policy_name
                            response_matches_ground_truth = True
                            logger.info(f"   ✅ Found expected keywords in response: {keywords_found}")
                        else:
                            logger.warning(
                                f"   ⚠️  Only found {len(keywords_found)}/{len(expected_keywords)} keywords: {keywords_found}"
                            )

                # Check for tool approval responses
                elif expected_type == "tool_approval":
                    # Tool approval interrupts execution - check if graph was interrupted
                    # The SDK returns "⏸️ Execution paused for approval..." when interrupted
                    if expected_keywords:
                        response_lower = response.lower()
                        keywords_found = [kw for kw in expected_keywords if kw.lower() in response_lower]

                        # Check if graph state indicates interruption
                        is_interrupted = False
                        try:
                            state = agent.graph.get_state({"configurable": {"thread_id": thread_id}})
                            is_interrupted = state and state.next and len(state.next) > 0
                            if is_interrupted:
                                logger.info(
                                    f"   ✅ Graph interrupted for approval (state.next = {state.next})"
                                )
                        except Exception as e:
                            logger.debug(f"   Could not check graph state: {e}")

                        # Check if response contains pause message (SDK returns this when interrupted)
                        has_pause_message = (
                            "execution paused" in response_lower
                            or "paused for approval" in response_lower
                            or ("approval" in response_lower and "action_response" in response_lower)
                        )

                        # Tool approval should either interrupt the graph OR contain approval keywords/pause message
                        if is_interrupted or has_pause_message or keywords_found:
                            matched = True
                            matched_policy_name = expected_policy_name
                            response_matches_ground_truth = True

                            # If graph is interrupted, we can approve and continue
                            if is_interrupted:
                                logger.info("   ✅ Graph interrupted for approval (as expected)")

                                # If we have expected_after_approval, approve and continue
                                if expected_after_approval:
                                    logger.info("   🔄 Approving tool execution and continuing...")
                                    from datetime import datetime
                                    from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
                                        ActionResponse,
                                        ActionType,
                                    )

                                    approval = ActionResponse(
                                        action_id="tool_approval",
                                        response_type=ActionType.CONFIRMATION,
                                        confirmed=True,
                                        timestamp=datetime.now().isoformat(),
                                        user_id=thread_id,
                                        session_id=thread_id,
                                    )

                                    # Resume execution using invoke with None and action_response
                                    logger.info("   ▶️  Resuming execution after approval...")
                                    result_after_approval = await agent.invoke(
                                        None, thread_id=thread_id, action_response=approval
                                    )
                                    response_after_approval = result_after_approval.answer
                                    logger.info(
                                        f"   Agent response after approval: {response_after_approval[:200]}..."
                                    )

                                    # Check if response contains expected keywords after approval
                                    response_after_lower = response_after_approval.lower()
                                    after_keywords_found = [
                                        kw
                                        for kw in expected_after_approval
                                        if kw.lower() in response_after_lower
                                    ]

                                    if len(after_keywords_found) >= len(expected_after_approval) * 0.5:
                                        logger.info(
                                            f"   ✅ Found expected keywords after approval: {after_keywords_found}"
                                        )
                                        response_matches_ground_truth = True
                                    else:
                                        logger.warning(
                                            f"   ⚠️  Only found {len(after_keywords_found)}/{len(expected_after_approval)} "
                                            f"keywords after approval: {after_keywords_found}"
                                        )
                                        response_matches_ground_truth = False

                                    # Update response for logging
                                    response = response_after_approval
                            elif has_pause_message:
                                logger.info("   ✅ Found pause message in response (as expected)")
                            elif keywords_found:
                                logger.info(f"   ✅ Found expected keywords in response: {keywords_found}")
                        else:
                            logger.warning(
                                f"   ⚠️  Graph not interrupted and approval keywords not found. Response: {response[:100]}"
                            )
            else:
                # For non-matching cases, verify that policy response keywords are NOT present
                # (This is a sanity check - we don't want false positives)
                if expected_keywords is None:
                    # No specific check needed for non-matching cases
                    matched = False
                    response_matches_ground_truth = True  # No ground truth to check
                else:
                    # If keywords are provided for non-matching, verify they're NOT in response
                    response_lower = response.lower()
                    unexpected_keywords = [kw for kw in expected_keywords if kw.lower() in response_lower]
                    if not unexpected_keywords:
                        response_matches_ground_truth = True
                        logger.info("   ✅ Confirmed policy keywords not present (as expected)")
                    else:
                        logger.warning(f"   ⚠️  Found unexpected policy keywords: {unexpected_keywords}")

            if matched:
                matches_found += 1
                if expected_policy_name:
                    matches_expected += 1
                    assert matched_policy_name == expected_policy_name, (
                        f"Expected policy '{expected_policy_name}' to match, but response suggests different behavior"
                    )

                    # Verify response matches ground truth
                    if response_matches_ground_truth:
                        response_checks_passed += 1
                        logger.info(
                            f"   ✅ Policy '{matched_policy_name}' matched correctly with ground truth response!"
                        )
                    else:
                        logger.warning("   ⚠️  Policy matched but response doesn't match ground truth")
                else:
                    logger.warning("   ⚠️  Unexpected match detected")
            else:
                if expected_policy_name:
                    logger.warning(
                        f"   ⚠️  Expected policy '{expected_policy_name}' to match but response doesn't indicate it"
                    )
                else:
                    if response_matches_ground_truth:
                        logger.info("   ✅ Correctly did not match (no policy expected)")
                    else:
                        logger.warning("   ⚠️  No match but response contains unexpected policy keywords")

        except Exception as e:
            logger.error(f"   ❌ Error testing utterance '{utterance}': {e}")
            if expected_policy_name:
                # If we expected a match but got an error, that's a problem
                raise

    logger.info("\n" + "=" * 80)
    logger.info("Test Summary:")
    logger.info(f"  - Total test cases: {len(test_cases)}")
    logger.info(f"  - Matches found: {matches_found}")
    logger.info(f"  - Expected matches: {sum(1 for case in test_cases if case[1])}")
    logger.info(f"  - Correct matches: {matches_expected}")
    logger.info(f"  - Response ground truth checks passed: {response_checks_passed}")
    logger.info("=" * 80)

    # Verify we got at least some matches
    assert matches_found > 0, "Expected at least one policy match"
    assert matches_expected > 0, "Expected at least one correct policy match"
    assert response_checks_passed >= matches_expected * 0.8, (
        f"Expected at least 80% of matched policies to have correct responses, got {response_checks_passed}/{matches_expected}"
    )
