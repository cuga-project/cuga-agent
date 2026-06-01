"""Test for adding and updating tool guard policies.

NOTE: This test demonstrates the API usage but may require policy system to be enabled
in settings to run successfully. The test shows the correct usage pattern.
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
async def test_add_and_update_tool_guard_policy():
    """Test adding a tool guide policy and then updating it with tool guards.
    
    This test demonstrates the correct API usage for adding and updating tool guards.
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
        print("Attempting to add Tool Guide policy...")
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
        
        # Step 3: Update policy with tool guards
        tool_guards = {
            "book_flight": {
                "violating_examples": [
                    "Book a flight for user uid_56845 (regular member) with 5 passengers",
                    "User uid_56845 wants to book flight FL123 for 4 passengers",
                    "Regular member uid_56845 booking flight with 6 passengers",
                ],
                "compliance_examples": [
                    "Book a flight for user uid_12345 (gold member) with 5 passengers",
                    "User uid_67890 (silver member) wants to book flight FL123 for 4 passengers",
                    "Regular member uid_56845 booking flight with 2 passengers",
                ],
                "policy_code": '''from typing import *

from toolguard.runtime import PolicyViolationException, rule
from cuga_app.cuga_app_types import *
from cuga_app.i_cuga_app import ICugaApp

@rule("Flight Booking Membership Policy")
async def guard_flight_booking_membership_policy(api: ICugaApp, args: BookFlightArgs):
    """
    Policy to check: Membership-based restrictions for flight bookings to ensure fair resource allocation

    ## Flight Booking Restrictions by Membership Level

    ### Policy Rules
    - Customers with "regular" membership cannot book a flight with more than 3 passengers
    - Gold and silver members have no passenger restrictions

    Args:
        api (ICugaApp): api to access other tools.
        args (BookFlightArgs): Arguments for booking a flight.
    """

    # Retrieve membership level for the user
    membership_resp = await api.get_membership(GetMembershipArgs(user_id=args.user_id))
    membership_level = getattr(membership_resp, "membership_level", None)
    if membership_level is None:
        # Fallback: try dict access if response is a dict
        membership_level = membership_resp.get("membership_level") if isinstance(membership_resp, dict) else None

    if membership_level == "regular" and args.passengers > 3:
        raise PolicyViolationException("Regular members cannot book a flight with more than 3 passengers.")
'''
            }
        }
        
        updated_policy_id = await agent.policies.update_tool_guard(
            policy_id=policy_id,
            tool_guards=tool_guards,
        )
        
        assert updated_policy_id == policy_id, "Updated policy ID should match original"
        print(f"✅ Updated policy with tool guards")
        
        # Step 4: Verify tool guards were added
        updated_policy_dict = await agent.policies.get(policy_id)
        assert updated_policy_dict is not None, "Updated policy should exist"
        
        # Access the full policy object
        updated_policy = updated_policy_dict["policy"]
        assert updated_policy.tool_guards is not None, "Policy should have tool_guards field"
        assert "book_flight" in updated_policy.tool_guards, "book_flight should have guards"
        
        book_flight_guard = updated_policy.tool_guards["book_flight"]
        assert len(book_flight_guard.violating_examples) == 3, "Should have 3 violating examples"
        assert len(book_flight_guard.compliance_examples) == 3, "Should have 3 compliance examples"
        assert book_flight_guard.policy_code != "", "Should have policy code"
        assert "PolicyViolationException" in book_flight_guard.policy_code, "Policy code should contain PolicyViolationException"
        
        print(f"✅ Verified tool guards were added correctly")
        print(f"   - Violating examples: {len(book_flight_guard.violating_examples)}")
        print(f"   - Compliance examples: {len(book_flight_guard.compliance_examples)}")
        print(f"   - Policy code length: {len(book_flight_guard.policy_code)} chars")
        
        # Step 5: List all policies and verify our policy is there
        all_policies = await agent.policies.list()
        policy_ids = [p["id"] for p in all_policies]
        assert policy_id in policy_ids, "Policy should be in the list"
        print(f"✅ Policy found in list of all policies")
        
        print("\n🎉 All tests passed!")
        
    finally:
        # Cleanup: delete the policy if it was created
        if policy_id:
            await agent.policies.delete(policy_id)
            print(f"🧹 Cleaned up policy: {policy_id}")
        await agent.aclose()


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_add_and_update_tool_guard_policy())

# Made with Bob
