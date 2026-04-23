"""
Debug script for creating a tool guard policy with code generation.

This script demonstrates:
1. Creating a CugaAgent with flight booking tools
2. Adding a tool guide policy for membership-based restrictions
3. Generating examples for the policy
4. Generating guard code for the policy
5. Updating the policy with examples and code
6. Exporting the policy data to a JSON file
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime

from langchain_core.tools import tool

from cuga import CugaAgent
from cuga.backend.cuga_graph.policy.models import ToolGuide


@tool
def book_flight(user_id: str, flight_id: str, passengers: int) -> str:
    """Book a flight for a user with specified number of passengers"""
    return f"Flight {flight_id} booked for user {user_id} with {passengers} passengers"


@tool
def get_membership(user_id: str) -> str:
    """Get the membership level of a user (gold, silver, or regular)"""
    # Simulate membership lookup
    memberships = {
        "user123": "gold",
        "user456": "silver",
        "user789": "regular"
    }
    return memberships.get(user_id, "regular")


async def main():
    """Main workflow for creating and managing a tool guard policy with code generation."""
    
    # Step 1: Create a CugaAgent with the flight booking tools
    print("Step 1: Creating CugaAgent with flight booking tools...")
    agent = CugaAgent(tools=[book_flight, get_membership])
    
    # Step 2: Add a new tool guide policy
    print("\nStep 2: Adding new tool guide policy...")
    policy_id = await agent.policies.add_tool_guide(
        name="Flight Booking Membership Policy",
        content="""## Flight Booking Restrictions by Membership Level

### Policy Rules
- Customers with "regular" membership cannot book a flight with more than 3 passengers
- Gold and silver members have no passenger restrictions
- This policy ensures fair resource allocation and encourages membership upgrades

### Validation Requirements
- Always check user membership level before booking
- Reject bookings that violate passenger limits
- Provide clear error messages when restrictions apply
""",
        target_tools=["book_flight"],
        description="Membership-based restrictions for flight bookings to ensure fair resource allocation",
    )
    print(f"Created policy with ID: {policy_id}")
    
    # Step 3: Generate examples using the SDK function
    print("\nStep 3: Generating tool guard examples...")
    violating_examples, compliance_examples = await agent.policies.generate_tool_guard_examples(
        policy_id=policy_id,
        target_tool="book_flight"
    )
    
    print(f"Generated {len(violating_examples)} violating examples and {len(compliance_examples)} compliance examples")
    print("\nViolating examples:")
    for i, example in enumerate(violating_examples, 1):
        print(f"  {i}. {example}")
    print("\nCompliance examples:")
    for i, example in enumerate(compliance_examples, 1):
        print(f"  {i}. {example}")
    
    # Step 4: Update the policy with generated examples
    print("\nStep 4: Updating policy with generated examples...")
    await agent.policies.update_tool_guard(
        policy_id=policy_id,
        tool_guards={
            "book_flight": {
                "description": "Guard rules for flight booking based on membership level",
                "violating_examples": violating_examples,
                "compliance_examples": compliance_examples,
                "policy_code": ""  # Will be populated in next step
            }
        }
    )
    print(f"Updated policy {policy_id} with generated examples")
    
    # Step 5: Generate guard code
    print("\nStep 5: Generating guard code...")
    guard_code = await agent.policies.generate_tool_guard_code(
        policy_id=policy_id,
        target_tool="book_flight",
        app_name="flight_booking_app"
    )
    
    print(f"Generated guard code ({len(guard_code)} characters)")
    print("\nGuard code preview (first 500 characters):")
    print(guard_code[:500])
    print("...")
    
    # Step 6: Update the policy with generated code
    print("\nStep 6: Updating policy with generated guard code...")
    await agent.policies.update_tool_guard(
        policy_id=policy_id,
        tool_guards={
            "book_flight": {
                "description": "Guard rules for flight booking based on membership level",
                "violating_examples": violating_examples,
                "compliance_examples": compliance_examples,
                "policy_code": guard_code
            }
        }
    )
    print(f"Updated policy {policy_id} with generated guard code")
    
    # Step 7: Save the policy
    print("\nStep 7: Saving policy...")
    policy_tool_guide = await agent.policies.get(policy_id)
    if policy_tool_guide is None:
        raise ValueError(f"Failed to retrieve policy {policy_id}")
    
    policy = policy_tool_guide["policy"]
    
    # Get policy system and update
    policy_system = await agent.policies._ensure_policy_system()
    if policy_system is None or policy_system.storage is None:
        raise ValueError("Policy system storage is not available")
    
    await policy_system.initialize()
    
    # Save to file system if available
    if agent.policies._fs_sync:
        agent.policies._fs_sync.save_policy_to_file(policy)
    
    print("Policy saved successfully!")
    
    # Step 8: Retrieve the policy from storage
    print("\nStep 8: Retrieving policy from storage...")
    retrieved_policy = await policy_system.storage.get_policy(policy_id)
    if retrieved_policy is None:
        raise ValueError(f"Failed to retrieve updated policy {policy_id}")
    
    if not isinstance(retrieved_policy, ToolGuide):
        raise TypeError(f"Expected ToolGuide, got {type(retrieved_policy).__name__}")
    
    print("Policy retrieved successfully!")
    
    # Step 9: Export to JSON file
    print("\nStep 9: Exporting policy data to JSON file...")
    
    # Convert tool_guards to serializable format
    tool_guards_data = None
    if retrieved_policy.tool_guards:
        tool_guards_data = {}
        for tool_name, guard in retrieved_policy.tool_guards.items():
            tool_guards_data[tool_name] = {
                "description": guard.description,
                "violating_examples": guard.violating_examples,
                "compliance_examples": guard.compliance_examples,
                "policy_code": guard.policy_code,
            }
    
    output_data = {
        "export_timestamp": datetime.utcnow().isoformat(),
        "policy_id": policy_id,
        "policy_name": retrieved_policy.name,
        "policy_description": retrieved_policy.description,
        "guide_content": retrieved_policy.guide_content,
        "target_tools": retrieved_policy.target_tools,
        "target_apps": retrieved_policy.target_apps,
        "tool_guards": tool_guards_data,
        "prepend": retrieved_policy.prepend,
        "priority": retrieved_policy.priority,
        "enabled": retrieved_policy.enabled,
        "policy_type": type(retrieved_policy).__name__,
        "metadata": retrieved_policy.metadata,
    }
    
    # Save to JSON file in current directory
    output_path = Path("flight_booking_policy_export.json")
    output_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
    
    print(f"\n{'='*60}")
    print("SUCCESS! Policy workflow with code generation completed:")
    print(f"{'='*60}")
    print(f"Policy ID: {policy_id}")
    print(f"Policy Name: {retrieved_policy.name}")
    print(f"Target Tools: {', '.join(retrieved_policy.target_tools)}")
    print(f"Generated Examples: {len(violating_examples)} violating, {len(compliance_examples)} compliance")
    print(f"Generated Code: {len(guard_code)} characters")
    print(f"\nExported to: {output_path.absolute()}")
    print(f"{'='*60}")
    
    # Print summary of tool_guards
    if retrieved_policy.tool_guards:
        print("\nTool Guards Summary:")
        for tool_name, guard in retrieved_policy.tool_guards.items():
            print(f"\n  Tool: {tool_name}")
            print(f"  Description: {guard.description}")
            print(f"  Violating Examples: {len(guard.violating_examples)}")
            print(f"  Compliance Examples: {len(guard.compliance_examples)}")
            print(f"  Policy Code Length: {len(guard.policy_code)} characters")


if __name__ == "__main__":
    asyncio.run(main())

# Made with Bob