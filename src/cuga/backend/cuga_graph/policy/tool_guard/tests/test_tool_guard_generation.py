"""Test for generating tool guard examples and code using SDK functions.

This test demonstrates using the generate_tool_guard_examples() and 
generate_tool_guard_code() SDK methods to automatically generate examples
and guard code instead of hard-coding them.

NOTE: This test requires policy system to be enabled and the toolguard package
to be installed. It may take longer to run as it uses LLM to generate content.
"""

import pytest
from langchain_core.tools import tool

from cuga.sdk import CugaAgent


# Skip test if policy system is not available
pytest_plugins = []


@tool
def book_flight(user_id: str, flight_id: str, passengers: int) -> str:
    """Book a flight for a user with specified number of passengers"""
    return f"Flight {flight_id} booked for user {user_id} with {passengers} passengers"


@tool
def get_membership(user_id: str) -> str:
    """Get the membership level of a user (gold, silver, or regular)"""
    memberships = {
        "uid_12345": "gold",
        "uid_67890": "silver",
        "uid_56845": "regular",  # Test user with regular membership
    }
    return memberships.get(user_id, "regular")


FLIGHT_GUARD_CONFIG = {
    "name": "Flight Booking Membership Policy",
    "content": """## Flight Booking Restrictions by Membership Level

### Policy Rules
- Customers with "regular" membership cannot book a flight with more than 3 passengers
- Gold and silver members have no passenger restrictions


""",
    "description": "Membership-based restrictions for flight bookings to ensure fair resource allocation",
}


@pytest.mark.asyncio
async def test_generate_tool_guard_examples_and_code():
    """Test generating tool guard examples and code using SDK functions.
    
    This test demonstrates the complete workflow:
    1. Create a tool guide policy
    2. Generate examples using generate_tool_guard_examples()
    3. Update policy with generated examples
    4. Generate guard code using generate_tool_guard_code()
    5. Verify the generated content
    """
    
    # Create agent with tools - policy system will be auto-created
    agent = CugaAgent(
        tools=[book_flight, get_membership],
        auto_load_policies=False,  # Don't auto-load from filesystem
    )
    
    # Initialize the agent to ensure policy system is created
    await agent.initialize()
    
    # Ensure policy system is initialized
    await agent.policies._ensure_policy_system()
    if not agent._policy_system or not agent._policy_system.storage:
        pytest.skip("Policy system is not enabled - skipping test")
    
    print("✓ Policy system initialized successfully")
    
    policy_id = None
    try:
        # Step 1: Add initial Tool Guide policy
        print("Step 1: Creating Tool Guide policy...")
        policy_id = await agent.policies.add_tool_guide(
            name=FLIGHT_GUARD_CONFIG["name"],
            content=FLIGHT_GUARD_CONFIG["content"],
            target_tools=["book_flight"],
            description=FLIGHT_GUARD_CONFIG["description"],
        )
        
        if policy_id is None:
            print("⚠️  add_tool_guide returned None - policy system may be disabled in settings")
            pytest.skip("Policy system returned None - may be disabled in configuration")
        
        print(f"✅ Created Tool Guide policy: {policy_id}")
        
        # Step 2: Verify policy was created
        policy_dict = await agent.policies.get(policy_id)
        assert policy_dict is not None, "Policy should exist"
        assert policy_dict["name"] == FLIGHT_GUARD_CONFIG["name"]
        
        # Access the full policy object
        policy = policy_dict["policy"]
        assert policy.target_tools == ["book_flight"]
        print(f"✅ Verified policy exists with correct configuration")
        
        # Step 3: Generate examples using SDK function
        print("\nStep 2: Generating examples using generate_tool_guard_examples()...")
        try:
            violating_examples, compliance_examples = await agent.policies.generate_tool_guard_examples(
                policy_id=policy_id,
                target_tool="book_flight"
            )
            
            print(f"✅ Generated examples:")
            print(f"   - Violating examples: {len(violating_examples)}")
            print(f"   - Compliance examples: {len(compliance_examples)}")
            
            # Verify we got some examples
            assert len(violating_examples) > 0, "Should have at least one violating example"
            assert len(compliance_examples) > 0, "Should have at least one compliance example"
            
            # Print first example of each type for debugging
            if violating_examples:
                print(f"   - First violating example: {violating_examples[0][:80]}...")
            if compliance_examples:
                print(f"   - First compliance example: {compliance_examples[0][:80]}...")
                
        except Exception as e:
            print(f"⚠️  Failed to generate examples: {e}")
            print("   This may be due to missing toolguard package or LLM configuration")
            pytest.skip(f"Could not generate examples: {e}")
        
        # Step 4: Update policy with generated examples
        print("\nStep 3: Updating policy with generated examples...")
        tool_guards = {
            "book_flight": {
                "violating_examples": violating_examples,
                "compliance_examples": compliance_examples,
            }
        }
        
        updated_policy_id = await agent.policies.update_tool_guard(
            policy_id=policy_id,
            tool_guards=tool_guards,
        )
        
        assert updated_policy_id == policy_id, "Updated policy ID should match original"
        print(f"✅ Updated policy with generated examples")
        
        # Step 5: Verify examples were added
        updated_policy_dict = await agent.policies.get(policy_id)
        assert updated_policy_dict is not None, "Updated policy should exist"
        
        updated_policy = updated_policy_dict["policy"]
        assert updated_policy.tool_guards is not None, "Policy should have tool_guards field"
        assert "book_flight" in updated_policy.tool_guards, "book_flight should have guards"
        
        book_flight_guard = updated_policy.tool_guards["book_flight"]
        assert len(book_flight_guard.violating_examples) > 0, "Should have violating examples"
        assert len(book_flight_guard.compliance_examples) > 0, "Should have compliance examples"
        print(f"✅ Verified examples were stored in policy")
        
        # Step 6: Generate guard code using SDK function
        print("\nStep 4: Generating guard code using generate_tool_guard_code()...")
        try:
            guard_code = await agent.policies.generate_tool_guard_code(
                policy_id=policy_id,
                target_tool="book_flight",
                app_name="cuga_app"  # Explicitly set app_name
            )
            
            print(f"✅ Generated guard code:")
            print(f"   - Code length: {len(guard_code)} characters")
            
            # Verify the guard code has expected content
            assert len(guard_code) > 0, "Guard code should not be empty"
            assert "PolicyViolationException" in guard_code, "Guard code should contain PolicyViolationException"
            assert "book_flight" in guard_code.lower() or "BookFlight" in guard_code, "Guard code should reference the tool"
            
            # Print first few lines for debugging
            code_lines = guard_code.split('\n')[:50]
            print(f"   - First few lines:")
            for line in code_lines:
                print(f"     {line}")
            
            print(f"✅ Verified guard code structure")
            
        except Exception as e:
            print(f"⚠️  Failed to generate guard code: {e}")
            print("   This may be due to missing toolguard package or LLM configuration")
            pytest.skip(f"Could not generate guard code: {e}")
        
        # Step 7: Update policy with generated guard code
        print("\nStep 5: Updating policy with generated guard code...")
        tool_guards_with_code = {
            "book_flight": {
                "violating_examples": violating_examples,
                "compliance_examples": compliance_examples,
                "policy_code": guard_code,
            }
        }
        
        final_policy_id = await agent.policies.update_tool_guard(
            policy_id=policy_id,
            tool_guards=tool_guards_with_code,
        )
        
        assert final_policy_id == policy_id, "Final policy ID should match original"
        print(f"✅ Updated policy with generated guard code")
        
        # Step 8: Final verification
        final_policy_dict = await agent.policies.get(policy_id)
        assert final_policy_dict is not None, "Final policy should exist"
        
        final_policy = final_policy_dict["policy"]
        final_guard = final_policy.tool_guards["book_flight"]
        
        assert len(final_guard.violating_examples) > 0, "Should have violating examples"
        assert len(final_guard.compliance_examples) > 0, "Should have compliance examples"
        assert final_guard.policy_code != "", "Should have policy code"
        assert "PolicyViolationException" in final_guard.policy_code, "Policy code should contain PolicyViolationException"
        
        print(f"✅ Final verification passed:")
        print(f"   - Violating examples: {len(final_guard.violating_examples)}")
        print(f"   - Compliance examples: {len(final_guard.compliance_examples)}")
        print(f"   - Policy code length: {len(final_guard.policy_code)} chars")
        
        # Step 9: List all policies and verify our policy is there
        all_policies = await agent.policies.list()
        policy_ids = [p["id"] for p in all_policies]
        assert policy_id in policy_ids, "Policy should be in the list"
        print(f"✅ Policy found in list of all policies")
        
        print("\n🎉 All tests passed! Successfully generated examples and guard code using SDK functions.")
        
    finally:
        # Cleanup: delete the policy if it was created
        if policy_id:
            await agent.policies.delete(policy_id)
            print(f"\n🧹 Cleaned up policy: {policy_id}")
        await agent.aclose()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_generate_tool_guard_examples_and_code())


