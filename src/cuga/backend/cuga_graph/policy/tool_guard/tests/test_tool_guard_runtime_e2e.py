"""
E2E test for tool guard runtime with code generation and policy enforcement.

This test demonstrates:
1. Creating a CugaAgent with flight booking tools
2. Adding multiple tool guide policies
3. Generating examples and guard code for each policy
4. Testing policies with ToolGuardRuntime
5. Cleaning up test policies at the end

Configuration:
- Set DELETE_ALL_POLICIES_AT_START = True to delete all existing policies before running
- Set DELETE_ALL_POLICIES_AT_START = False to preserve existing policies (default)
- Set environment variable CUGA_E2E_ALLOW_DESTRUCTIVE=true to enable destructive cleanup
"""

import os
from pathlib import Path

import pytest
from langchain_core.tools import tool

from cuga import CugaAgent
from cuga.backend.cuga_graph.policy.tool_guard.tool_guard_runtime import ToolGuardRuntime

# ============================================================================
# CONFIGURATION
# ============================================================================
# Default to False for safety - require explicit opt-in for destructive operations
DELETE_ALL_POLICIES_AT_START = os.environ.get("CUGA_E2E_ALLOW_DESTRUCTIVE", "").lower() in ("true", "1", "yes")
# ============================================================================

# Define policies to create
POLICIES = [
    {
        "name": "Flight Booking Membership Policy",
        "content": """## Flight Booking Restrictions by Membership Level

### Policy Rules
- Customers with "regular" membership cannot book a flight with more than 3 passengers
- Gold and silver members have no passenger restrictions
- This policy ensures fair resource allocation and encourages membership upgrades

### Validation Requirements
- Always check user membership level before booking
- Reject bookings that violate passenger limits
- Provide clear error messages when restrictions apply
""",
        "description": "Membership-based restrictions for flight bookings to ensure fair resource allocation",
    },
    {
        "name": "Flight ID Format Policy",
        "content": """## Flight ID Format Requirements

### Policy Rules
- Flight ID must start with exactly 2 letters
- Flight ID must have a total of exactly 4 characters (2 letters + 2 digits)
- Example valid flight IDs: FL12, AB99, XY01
- Example invalid flight IDs: F123 (only 1 letter), FLI2 (3 letters), FL1 (only 3 characters total)

### Validation Requirements
- Always validate flight ID format before booking
- Reject bookings with invalid flight ID format
- Provide clear error messages when format is incorrect
""",
        "description": "Flight ID format validation to ensure proper booking system compatibility",
    },
]


@tool
def book_flight(user_id: str, flight_id: str, passengers: int) -> str:
    """Book a flight for a user with specified number of passengers"""
    return f"Flight {flight_id} booked for user {user_id} with {passengers} passengers"


@tool
def get_membership(user_id: str) -> str:
    """Get the membership level of a user (gold, silver, or regular)"""
    memberships = {
        "user123": "gold",
        "user456": "silver",
        "user789": "regular"
    }
    return memberships.get(user_id, "regular")


async def cleanup_all_policies(agent):
    """Clean up all existing policies if configured."""
    print("="*60)
    print("Step 0: Cleaning up ALL existing policies")
    print("="*60)
    
    policy_system = await agent.policies._ensure_policy_system()
    if policy_system is None or policy_system.storage is None:
        raise ValueError("Policy system storage is not available")
    
    await policy_system.initialize()
    
    # Delete from storage
    all_policies = await policy_system.storage.list_policies()
    print(f"Found {len(all_policies)} total policies in storage")
    
    for policy in all_policies:
        await policy_system.storage.delete_policy(policy.id)
        print(f"  Deleted from storage: '{policy.name}' (ID: {policy.id})")
    
    # Delete from filesystem
    if agent.policies._fs_sync:
        print("\nCleaning up policy files from filesystem...")
        cuga_folder = Path(agent.policies._fs_sync.cuga_folder)
        if cuga_folder.exists():
            policy_subfolders = ['playbooks', 'output_formatters', 'tool_guides', 
                               'intent_guards', 'tool_approvals', 'policies']
            
            total_deleted = 0
            for subfolder in policy_subfolders:
                subfolder_path = cuga_folder / subfolder
                if subfolder_path.exists():
                    files = list(subfolder_path.glob("*.md")) + list(subfolder_path.glob("*.json"))
                    for file in files:
                        file.unlink()
                        total_deleted += 1
            
            if total_deleted > 0:
                print(f"✅ Deleted {total_deleted} policy files from filesystem")
    
    print("✅ All policies successfully deleted")
    print("="*60)


async def create_and_process_policies(agent, policy_system):
    """Create policies and generate examples and guard code for each."""
    print("\nStep 1: Creating and processing policies...")
    print("="*60)
    
    policy_data = []
    
    for idx, policy_config in enumerate(POLICIES, 1):
        print(f"\n--- Processing Policy {idx}/{len(POLICIES)}: {policy_config['name']} ---")
        
        # Create policy
        print(f"Creating policy...")
        policy_id = await agent.policies.add_tool_guide(
            name=policy_config["name"],
            content=policy_config["content"],
            target_tools=["book_flight"],
            description=policy_config["description"],
        )
        print(f"✅ Created policy with ID: {policy_id}")
        
        # Generate examples
        print(f"Generating examples...")
        violating_examples, compliance_examples = await agent.policies.generate_tool_guard_examples(
            policy_id=policy_id,
            target_tool="book_flight"
        )
        print(f"✅ Generated {len(violating_examples)} violating and {len(compliance_examples)} compliance examples")
        
        # Print examples for debugging
        print(f"\n📋 Violating Examples:")
        for i, example in enumerate(violating_examples, 1):
            print(f"  {i}. {example}")
        
        print(f"\n📋 Compliance Examples:")
        for i, example in enumerate(compliance_examples, 1):
            print(f"  {i}. {example}")
        
        # Update policy with examples
        await agent.policies.update_tool_guard(
            policy_id=policy_id,
            tool_guards={
                "book_flight": {
                    "description": f"Guard rules for {policy_config['name']}",
                    "violating_examples": violating_examples,
                    "compliance_examples": compliance_examples,
                    "policy_code": ""
                }
            }
        )
        
        # Generate guard code
        print(f"Generating guard code...")
        guard_code = await agent.policies.generate_tool_guard_code(
            policy_id=policy_id,
            target_tool="book_flight",
            app_name="test_app"
        )
        print(f"✅ Generated guard code ({len(guard_code)} characters)")
        
        # Print guard code for debugging
        print(f"\n📝 Generated Guard Code:")
        print("="*60)
        print(guard_code)
        print("="*60)
        
        # Update policy with guard code
        await agent.policies.update_tool_guard(
            policy_id=policy_id,
            tool_guards={
                "book_flight": {
                    "description": f"Guard rules for {policy_config['name']}",
                    "violating_examples": violating_examples,
                    "compliance_examples": compliance_examples,
                    "policy_code": guard_code
                }
            }
        )
        
        # Save policy
        policy_tool_guide = await agent.policies.get(policy_id)
        if policy_tool_guide is None:
            raise ValueError(f"Failed to retrieve policy {policy_id}")
        
        policy = policy_tool_guide["policy"]
        
        print(f"✅ Policy saved successfully")
        
        # Store policy data for later use
        policy_data.append({
            "id": policy_id,
            "name": policy_config["name"],
            "policy": policy
        })
    
    print("\n" + "="*60)
    print(f"✅ All {len(POLICIES)} policies created and processed successfully")
    print("="*60)
    
    return policy_data


async def run_tests(tool_guard_runtime):
    """Run test cases to validate policy enforcement."""
    print(f"\n{'='*60}")
    print("Step 2: Testing policy enforcement with ToolGuardRuntime")
    print(f"{'='*60}")
    
    print(f"\nRuntime initialized with guards for: {tool_guard_runtime.get_guarded_tools()}")
    
    test_cases = [
        {
            "name": "Test Case 1: Too Many Passengers",
            "args": {"flight_id": "FL12", "user_id": "user789", "passengers": 8},
            "expected": "BLOCKED",
            "reason": "user789 is 'regular' member, 8 > 3 passengers"
        },
        {
            "name": "Test Case 2: Valid Booking",
            "args": {"flight_id": "FL45", "user_id": "user789", "passengers": 2},
            "expected": "ALLOWED",
            "reason": "user789 is 'regular' member, 2 <= 3 passengers, valid flight ID"
        },
        {
            "name": "Test Case 3: Gold Member",
            "args": {"flight_id": "AB78", "user_id": "user123", "passengers": 10},
            "expected": "ALLOWED",
            "reason": "user123 is 'gold' member, no passenger limit"
        },
        {
            "name": "Test Case 4: Multiple Violations",
            "args": {"flight_id": "F123", "user_id": "user789", "passengers": 8},
            "expected": "BLOCKED",
            "reason": "8 > 3 passengers AND flight_id 'F123' has only 1 letter"
        },
        {
            "name": "Test Case 5: Invalid Flight ID Only",
            "args": {"flight_id": "ABC1", "user_id": "user789", "passengers": 2},
            "expected": "BLOCKED",
            "reason": "flight_id 'ABC1' has 3 letters instead of 2"
        },
    ]
    
    results = []
    for test in test_cases:
        print(f"\n--- {test['name']} ---")
        print(f"Attempting: book_flight({', '.join(f'{k}={repr(v)}' for k, v in test['args'].items())})")
        print(f"Expected: {test['expected']} ({test['reason']})")
        
        try:
            error = await tool_guard_runtime.guard_tool_call(
                app_name="test_app",
                function_name="book_flight",
                arguments=test["args"]
            )
            
            actual = "BLOCKED" if error else "ALLOWED"
            success = actual == test["expected"]
            
            if success:
                print(f"\n✅ SUCCESS: Tool call was correctly {actual}!")
                if error:
                    print(f"Error message: {error}")
                else:
                    # Actually invoke the tool
                    result = await book_flight.ainvoke(test["args"])
                    print(f"Tool result: {result}")
            else:
                print(f"\n⚠️  WARNING: Tool call was {actual} (expected {test['expected']})")
                if error:
                    print(f"Error message: {error}")
            
            results.append({"test": test["name"], "success": success, "actual": actual})
            
        except Exception as e:
            print(f"\n❌ Error during validation: {type(e).__name__}: {e}")
            results.append({"test": test["name"], "success": False, "actual": "ERROR"})
    
    # Print summary
    print(f"\n{'='*60}")
    print("Test Summary:")
    print(f"{'='*60}")
    passed = sum(1 for r in results if r["success"])
    print(f"Passed: {passed}/{len(results)}")
    for r in results:
        status = "✅" if r["success"] else "❌"
        print(f"  {status} {r['test']}: {r['actual']}")
    print(f"{'='*60}")
    
    return results


async def cleanup_policies(agent, policy_system, policy_data):
    """Delete all created policies."""
    print(f"\n{'='*60}")
    print("Step 3: Cleaning up test policies")
    print(f"{'='*60}")
    
    try:
        for policy_info in policy_data:
            # Delete from storage
            await policy_system.storage.delete_policy(policy_info["id"])
            print(f"✅ Deleted '{policy_info['name']}' from storage")
            
            # Delete from filesystem
            if agent.policies._fs_sync:
                cuga_folder = Path(agent.policies._fs_sync.cuga_folder)
                tool_guides_folder = cuga_folder / "tool_guides"
                
                if tool_guides_folder.exists():
                    policy_files = list(tool_guides_folder.glob(f"*{policy_info['id']}*.md")) + \
                                  list(tool_guides_folder.glob(f"*{policy_info['id']}*.json"))
                    
                    for policy_file in policy_files:
                        policy_file.unlink()
                        print(f"✅ Deleted file: {policy_file.name}")
        
        print("✅ All test policies successfully deleted")
        
    except Exception as e:
        print(f"⚠️  Error during cleanup: {type(e).__name__}: {e}")
    
    print(f"{'='*60}")


@pytest.mark.asyncio
async def test_tool_guard_runtime_e2e():
    """
    E2E test for tool guard runtime with policy creation, code generation, and enforcement.
    
    This test:
    1. Creates a CugaAgent with flight booking tools
    2. Optionally cleans up all existing policies (if CUGA_E2E_ALLOW_DESTRUCTIVE=true)
    3. Creates multiple tool guide policies
    4. Generates examples and guard code for each policy
    5. Tests policy enforcement with ToolGuardRuntime
    6. Cleans up test policies
    """
    
    # Step 0: Create agent and optional cleanup
    agent = CugaAgent(tools=[book_flight, get_membership])
    
    if DELETE_ALL_POLICIES_AT_START:
        await cleanup_all_policies(agent)
    else:
        print("="*60)
        print("Skipping initial cleanup (DELETE_ALL_POLICIES_AT_START=False)")
        print("To enable: export CUGA_E2E_ALLOW_DESTRUCTIVE=true")
        print("="*60)
    
    # Get policy system
    policy_system = await agent.policies._ensure_policy_system()
    if policy_system is None or policy_system.storage is None:
        pytest.skip("Policy system storage is not available")
    
    await policy_system.initialize()
    
    # Step 1: Create and process policies
    policy_data = await create_and_process_policies(agent, policy_system)
    
    # Step 2: Initialize runtime and run tests
    # Construct domain_dir from cuga_folder
    domain_dir = None
    if agent.cuga_folder:
        domain_dir = Path(agent.cuga_folder) / "toolguard" / "domain"
    
    tool_guard_runtime = ToolGuardRuntime(
        tool_provider=agent.tool_provider,
        enable_policies=True,
        policy_storage=policy_system.storage,
        domain_dir=domain_dir
    )
    await tool_guard_runtime.initialize()
    
    results = await run_tests(tool_guard_runtime)
    
    # Step 3: Cleanup
    await cleanup_policies(agent, policy_system, policy_data)
    
    # Shutdown runtime
    await tool_guard_runtime.shutdown()
    
    # Print comprehensive summary
    print_comprehensive_summary(policy_data, results)
    
    # Assert that all tests passed
    passed = sum(1 for r in results if r["success"])
    assert passed == len(results), f"Only {passed}/{len(results)} tests passed"


def print_comprehensive_summary(policy_data, test_results):
    """Print a comprehensive summary of the E2E test including examples, code, and results."""
    print(f"\n{'='*80}")
    print("📊 COMPREHENSIVE E2E TEST SUMMARY")
    print(f"{'='*80}")
    
    # Section 1: Policies Overview
    print(f"\n{'─'*80}")
    print("1️⃣  POLICIES CREATED")
    print(f"{'─'*80}")
    for idx, policy_info in enumerate(policy_data, 1):
        policy = policy_info["policy"]
        print(f"\n[Policy {idx}] {policy.name}")
        print(f"  ID: {policy.id}")
        print(f"  Description: {policy.description}")
        print(f"  Target Tools: {', '.join(policy.target_tools) if policy.target_tools else 'None'}")
        print(f"  Enabled: {policy.enabled}")
    
    # Section 2: Generated Examples
    print(f"\n{'─'*80}")
    print("2️⃣  GENERATED EXAMPLES")
    print(f"{'─'*80}")
    for idx, policy_info in enumerate(policy_data, 1):
        policy = policy_info["policy"]
        print(f"\n[Policy {idx}] {policy.name}")
        
        if policy.tool_guards and "book_flight" in policy.tool_guards:
            tool_guard = policy.tool_guards["book_flight"]
            
            # Violating examples
            print(f"\n  🚫 Violating Examples ({len(tool_guard.violating_examples or [])}):")
            for i, example in enumerate(tool_guard.violating_examples or [], 1):
                print(f"     {i}. {example}")
            
            # Compliance examples
            print(f"\n  ✅ Compliance Examples ({len(tool_guard.compliance_examples or [])}):")
            for i, example in enumerate(tool_guard.compliance_examples or [], 1):
                print(f"     {i}. {example}")
        else:
            print("  ⚠️  No tool guards found")
    
    # Section 3: Generated Guard Code
    print(f"\n{'─'*80}")
    print("3️⃣  GENERATED GUARD CODE")
    print(f"{'─'*80}")
    for idx, policy_info in enumerate(policy_data, 1):
        policy = policy_info["policy"]
        print(f"\n[Policy {idx}] {policy.name}")
        
        if policy.tool_guards and "book_flight" in policy.tool_guards:
            tool_guard = policy.tool_guards["book_flight"]
            
            if tool_guard.policy_code:
                print(f"\n  📝 Guard Code ({len(tool_guard.policy_code)} characters):")
                print(f"  {'-'*76}")
                # Indent each line of the code
                for line in tool_guard.policy_code.split('\n'):
                    print(f"  {line}")
                print(f"  {'-'*76}")
            else:
                print("  ⚠️  No policy code generated")
        else:
            print("  ⚠️  No tool guards found")
    
    # Section 4: Test Results
    print(f"\n{'─'*80}")
    print("4️⃣  RUNTIME TEST RESULTS")
    print(f"{'─'*80}")
    
    passed = sum(1 for r in test_results if r["success"])
    total = len(test_results)
    pass_rate = (passed / total * 100) if total > 0 else 0
    
    print(f"\n  Overall: {passed}/{total} tests passed ({pass_rate:.1f}%)")
    print(f"\n  Detailed Results:")
    for idx, result in enumerate(test_results, 1):
        status_icon = "✅" if result["success"] else "❌"
        print(f"    {idx}. {status_icon} {result['test']}")
        print(f"       Result: {result['actual']}")
    
    # Section 5: Final Status
    print(f"\n{'─'*80}")
    print("5️⃣  FINAL STATUS")
    print(f"{'─'*80}")
    
    if passed == total:
        print(f"\n  🎉 SUCCESS! All {total} tests passed!")
        print(f"  ✅ Policy creation: WORKING")
        print(f"  ✅ Example generation: WORKING")
        print(f"  ✅ Guard code generation: WORKING")
        print(f"  ✅ Runtime enforcement: WORKING")
    else:
        print(f"\n  ⚠️  PARTIAL SUCCESS: {passed}/{total} tests passed")
        failed = [r for r in test_results if not r["success"]]
        print(f"  Failed tests:")
        for r in failed:
            print(f"    - {r['test']}: {r['actual']}")
    
    print(f"\n{'='*80}")
    print("✅ E2E TEST COMPLETED")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_tool_guard_runtime_e2e())


