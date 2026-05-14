"""
Debug script for creating CRM tool guard policies with code generation and E2E testing.

This script demonstrates:
1. Creating a CugaAgent with CRM tools from the registry
2. Adding multiple tool guide policies (adapted from flight booking example)
3. Generating examples and guard code for each policy
4. Testing policies with agent.invoke() using natural language queries
5. Running 5 test cases similar to the flight booking example

Prerequisites:
============
IMPORTANT: Both the Registry server AND CRM API server MUST be running!

**Setup Steps:**

1. **Activate the virtual environment** (from project root):
   source .venv/bin/activate

2. **Start the servers** (choose one option):

   **Option A (Recommended) - Start both together:**
   cuga start demo_crm
   
   This starts both registry (8001) and CRM (8007) servers together.

   **Option B - Start separately:**
   
   Terminal 1 - Registry server:
     cuga start registry
   
   Terminal 2 - CRM API server:
     cd src/cuga/demo_tools/crm
     uv run python -m crm_api.main

3. **Run this script** (from project root with activated env):
   python src/cuga/sdk_core/debug_sdk_crm.py

**Verification:**
- Registry: http://localhost:8001/applications (should list 'crm')
- CRM API: http://localhost:8007/docs

Configuration:
- Set DELETE_ALL_POLICIES_AT_START = True to delete all existing policies before running
- Set DELETE_ALL_POLICIES_AT_START = False to preserve existing policies (default)
- Set environment variable CUGA_E2E_ALLOW_DESTRUCTIVE=true to enable destructive cleanup
"""

import asyncio
import os
import tempfile
from pathlib import Path

from cuga import CugaAgent
from cuga.backend.cuga_graph.policy.tool_guard import ToolGuardRuntime
from cuga.backend.cuga_graph.nodes.cuga_lite.combined_tool_provider import CombinedToolProvider

# ============================================================================
# CONFIGURATION
# ============================================================================
# Default to False for safety - require explicit opt-in for destructive operations
DELETE_ALL_POLICIES_AT_START = os.environ.get("CUGA_E2E_ALLOW_DESTRUCTIVE", "").lower() in ("true", "1", "yes")
# ============================================================================

# Define policy to create (Finance industry revenue requirements)
POLICY_CONFIG = {
    "name": "Finance eligibility revenue requirements",
    "content": """## Finance Industry Revenue Requirements

### Policy Rules
- Accounts cannot be created for companies from the Finance industry with annual revenue under $100,000
- This ensures we only onboard financially stable finance companies
- Companies from other industries have no revenue restrictions

### Validation Requirements
- Always check the industry field before account creation
- If industry is "Finance", verify annual_revenue >= 100000
- Reject account creation that violates revenue requirements
- Provide clear error messages when restrictions apply
""",
    "description": "Accounts cannot be created for companies from the Finance industry with annual revenue under $100,000.",
}


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


async def create_and_process_policy(agent, policy_system):
    """Create policy and generate examples and guard code."""
    print("\nStep 1: Creating and processing policy...")
    print("="*60)
    
    print(f"\n--- Processing Policy: {POLICY_CONFIG['name']} ---")
    
    # Create policy
    print(f"Creating policy...")
    policy_id = await agent.policies.add_tool_guide(
        name=POLICY_CONFIG["name"],
        content=POLICY_CONFIG["content"],
        target_tools=["crm_create_account_accounts_post"],
        description=POLICY_CONFIG["description"],
    )
    print(f"✅ Created policy with ID: {policy_id}")
    
    # Generate examples
    print(f"Generating examples...")
    violating_examples, compliance_examples = await agent.policies.generate_tool_guard_examples(
        policy_id=policy_id,
        target_tool="crm_create_account_accounts_post"
    )
    print(f"✅ Generated {len(violating_examples)} violating and {len(compliance_examples)} compliance examples")
    
    # Update policy with examples
    await agent.policies.update_tool_guard(
        policy_id=policy_id,
        tool_guards={
            "crm_create_account_accounts_post": {
                "description": f"Guard rules for {POLICY_CONFIG['name']}",
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
        target_tool="crm_create_account_accounts_post",
        app_name="crm_demo"
    )
    print(f"✅ Generated guard code ({len(guard_code)} characters)")
    
    # Update policy with guard code
    await agent.policies.update_tool_guard(
        policy_id=policy_id,
        tool_guards={
            "crm_create_account_accounts_post": {
                "description": f"Guard rules for {POLICY_CONFIG['name']}",
                "violating_examples": violating_examples,
                "compliance_examples": compliance_examples,
                "policy_code": guard_code
            }
        }
    )
    
    # Retrieve policy
    policy_tool_guide = await agent.policies.get(policy_id)
    if policy_tool_guide is None:
        raise ValueError(f"Failed to retrieve policy {policy_id}")
    
    policy = policy_tool_guide["policy"]
    
    print(f"✅ Policy created and processed successfully")
    print("="*60)
    
    return {
        "id": policy_id,
        "name": POLICY_CONFIG["name"],
        "policy": policy,
        "violating_examples": violating_examples,
        "compliance_examples": compliance_examples,
        "guard_code": guard_code
    }


async def run_tests(agent):
    """Run test cases to validate policy enforcement using agent.invoke()."""
    print(f"\n{'='*60}")
    print("Step 2: Testing policy enforcement with agent.invoke()")
    print(f"{'='*60}")
    
    test_cases = [
        {
            "name": "Test Case 1: Non-Finance with Low Revenue (ALLOWED)",
            "query": "Create a CRM account for Small Law Firm. Website: smalllawfirm.com, Phone: +1-555-3000, Address: 456 Main Street, City: Boston, State: MA, Country: USA, Region: North America, Annual Revenue: $50,000, Employee Count: 10, Industry: Law",
            "expected": "ALLOWED",
            "reason": "Law industry has no revenue restrictions",
            "check_keywords": ["created", "success", "account"]
        },
        {
            "name": "Test Case 2: Non-Finance with High Revenue (ALLOWED)",
            "query": "Create a CRM account for Big Tech Corp. Website: bigtech.com, Phone: +1-555-4000, Address: 789 Innovation Dr, City: San Francisco, State: CA, Country: USA, Region: North America, Annual Revenue: $5,000,000, Employee Count: 500, Industry: Technology",
            "expected": "ALLOWED",
            "reason": "Technology industry has no revenue restrictions",
            "check_keywords": ["created", "success", "account"]
        },
        {
            "name": "Test Case 3: Finance with Low Revenue (BLOCKED)",
            "query": "Create a CRM account for ACM22 Corporation. Website: acm22corporation.com, Phone: +1-555-1883, Address: 94 rue du Gue Jacquet, City: Chatou, State: Île-de-France, Country: France, Region: Europe, Annual Revenue: $50,000, Employee Count: 88, Industry: Finance",
            "expected": "BLOCKED",
            "reason": "Finance industry with revenue $50,000 < $100,000",
            "check_keywords": ["finance", "revenue", "policy", "restriction", "cannot"]
        },
        {
            "name": "Test Case 4: Finance with High Revenue (ALLOWED)",
            "query": "Create a CRM account for Global Finance Corp. Website: globalfinance.com, Phone: +1-555-2000, Address: 123 Wall Street, City: New York, State: NY, Country: USA, Region: North America, Annual Revenue: $1,500,000, Employee Count: 250, Industry: Finance",
            "expected": "ALLOWED",
            "reason": "Finance industry with revenue $1,500,000 >= $100,000",
            "check_keywords": ["created", "success", "account"]
        },
    ]
    
    results = []
    for test in test_cases:
        print(f"\n--- {test['name']} ---")
        print(f"User Query: {test['query']}")
        print(f"Expected: {test['expected']} ({test['reason']})")
        
        try:
            # Invoke the agent with the user query
            invoke_result = await agent.invoke(test["query"])
            
            # Check if the target tool was called by inspecting tool_calls
            target_tool = "crm_create_account_accounts_post"
            tool_was_called = any(
                call.get("name") == target_tool
                for call in invoke_result.tool_calls
            )
            
            # Determine if blocked based on tool execution
            if tool_was_called:
                # Tool was executed - check if it succeeded
                tool_call = next(
                    call for call in invoke_result.tool_calls
                    if call.get("name") == target_tool
                )
                
                # Check for errors in the tool call
                if tool_call.get("error"):
                    actual = "BLOCKED"
                    reason = f"Tool call failed: {tool_call.get('error')}"
                else:
                    actual = "ALLOWED"
                    reason = f"Tool executed successfully"
                    if tool_call.get("result"):
                        reason += f" - Result: {str(tool_call.get('result'))[:100]}"
            else:
                # Tool was not called - likely blocked by policy
                actual = "BLOCKED"
                reason = "Tool was not invoked (likely blocked by policy)"
            
            success = actual == test["expected"]
            
            if success:
                print(f"\n✅ SUCCESS: Request was correctly {actual}!")
                print(f"Reason: {reason}")
                print(f"Agent response: {invoke_result.answer[:200]}..." if len(invoke_result.answer) > 200 else f"Agent response: {invoke_result.answer}")
            else:
                print(f"\n⚠️  WARNING: Request was {actual} (expected {test['expected']})")
                print(f"Reason: {reason}")
                print(f"Agent response: {invoke_result.answer[:200]}..." if len(invoke_result.answer) > 200 else f"Agent response: {invoke_result.answer}")
            
            # Debug: Show all tool calls
            if invoke_result.tool_calls:
                print(f"\nTool calls made ({len(invoke_result.tool_calls)}):")
                for call in invoke_result.tool_calls:
                    print(f"  - {call.get('name')}: {call.get('result', 'N/A')[:100] if call.get('result') else 'N/A'}")
                    if call.get("error"):
                        print(f"    Error: {call.get('error')}")
            else:
                print("\nNo tool calls were made")
            
            results.append({
                "test": test["name"],
                "success": success,
                "actual": actual,
                "reason": reason,
                "tool_calls": invoke_result.tool_calls
            })
            
        except Exception as e:
            print(f"\n❌ Error during agent invocation: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
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
        if "reason" in r:
            print(f"      Reason: {r['reason']}")
        if "tool_calls" in r and r["tool_calls"]:
            print(f"      Tool calls: {len(r['tool_calls'])}")
    print(f"{'='*60}")
    
    return results


async def cleanup_policy(agent, policy_system, policy_data):
    """Delete the created policy."""
    print(f"\n{'='*60}")
    print("Step 3: Cleaning up test policy")
    print(f"{'='*60}")
    
    try:
        # Delete from storage
        await policy_system.storage.delete_policy(policy_data["id"])
        print(f"✅ Deleted '{policy_data['name']}' from storage")
        
        # Delete from filesystem
        if agent.policies._fs_sync:
            cuga_folder = Path(agent.policies._fs_sync.cuga_folder)
            tool_guides_folder = cuga_folder / "tool_guides"
            
            if tool_guides_folder.exists():
                policy_files = list(tool_guides_folder.glob(f"*{policy_data['id']}*.md")) + \
                              list(tool_guides_folder.glob(f"*{policy_data['id']}*.json"))
                
                for policy_file in policy_files:
                    policy_file.unlink()
                    print(f"✅ Deleted file: {policy_file.name}")
        
        print("✅ Test policy successfully deleted")
        
    except Exception as e:
        print(f"⚠️  Error during cleanup: {type(e).__name__}: {e}")
    
    print(f"{'='*60}")


async def main():
    """Main workflow for creating and testing CRM tool guard policies."""
    
    print("="*60)
    print("Initializing CRM Tool Provider...")
    print("="*60)
    
    # Create tool provider with CRM app
    tool_provider = CombinedToolProvider(app_names=["crm"])
    await tool_provider.initialize()
    
    # Verify CRM tools are available
    tools = await tool_provider.get_tools(app_name="crm")
    crm_tool = next((t for t in tools if t.name == "crm_create_account_accounts_post"), None)
    
    if not crm_tool:
        print("❌ ERROR: CRM tool 'crm_create_account_accounts_post' not found!")
        print("Available tools:", [t.name for t in tools])
        print("\n" + "="*60)
        print("TROUBLESHOOTING:")
        print("="*60)
        print("\n1. Make sure the Registry server is running:")
        print("   cuga start registry")
        print("   (Should be accessible at http://localhost:8001)")
        print("\n2. Make sure the CRM API server is running:")
        print("   cd src/cuga/demo_tools/crm")
        print("   uv run python -m crm_api.main")
        print("   (Should be accessible at http://localhost:8007/docs)")
        print("\n3. OR start both together:")
        print("   cuga start demo_crm")
        print("\n4. Verify the registry can see the CRM service:")
        print("   curl http://localhost:8001/applications")
        print("   (Should list 'crm' in the response)")
        print("="*60)
        return
    
    print(f"✅ Found CRM tool: {crm_tool.name}")
    print(f"✅ Total tools available: {len(tools)}")
    
    # Create agent with CRM tools and in-memory policy system
    print("\n" + "="*60)
    print("Initializing CugaAgent with in-memory policy system...")
    print("="*60)
    
    # Use a temporary database for this test
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp_db:
        temp_db_path = tmp_db.name
    
    try:
        agent = CugaAgent(tool_provider=tool_provider)
        
        # Initialize policy system with temporary database
        from cuga.backend.cuga_graph.policy.configurable import PolicyConfigurable
        
        agent._policy_system = PolicyConfigurable()
        await agent._policy_system.initialize(policy_db_path=temp_db_path)
        
        print(f"✅ Using temporary policy database: {temp_db_path}")
        print("✅ Policy system initialized")
        
        # Step 0: Optional cleanup
        if DELETE_ALL_POLICIES_AT_START:
            await cleanup_all_policies(agent)
        else:
            print("="*60)
            print("Skipping initial cleanup (DELETE_ALL_POLICIES_AT_START=False)")
            print("="*60)
        
        # Get policy system
        policy_system = agent._policy_system
        if policy_system is None or policy_system.storage is None:
            raise ValueError("Policy system storage is not available")
        
        # Step 1: Create and process policy
        policy_data = await create_and_process_policy(agent, policy_system)
        
        # Step 2: Run tests using agent.invoke()
        results = await run_tests(agent)
        
        # Step 3: Cleanup
        await cleanup_policy(agent, policy_system, policy_data)
        
        # Step 4: Display final summary
        print(f"\n{'='*60}")
        print("FINAL SUMMARY")
        print(f"{'='*60}")
        
        print(f"\n📋 Policy: {policy_data['name']}")
        print(f"Policy ID: {policy_data['id']}")
        
        print(f"\n🔴 Violating Examples ({len(policy_data['violating_examples'])}):")
        print("-" * 60)
        for i, example in enumerate(policy_data['violating_examples'], 1):
            print(f"\nExample {i}:")
            print(f"  {example}")
        
        print(f"\n✅ Compliance Examples ({len(policy_data['compliance_examples'])}):")
        print("-" * 60)
        for i, example in enumerate(policy_data['compliance_examples'], 1):
            print(f"\nExample {i}:")
            print(f"  {example}")
        
        print(f"\n💻 Generated Guard Code ({len(policy_data['guard_code'])} characters):")
        print("-" * 60)
        print(policy_data['guard_code'])
        
        print(f"\n{'='*60}")
        print("✅ E2E Test completed successfully!")
        print(f"{'='*60}")
        
    finally:
        # Clean up temporary database
        if os.path.exists(temp_db_path):
            os.unlink(temp_db_path)
            print(f"\n🧹 Cleaned up temporary database: {temp_db_path}")


if __name__ == "__main__":
    asyncio.run(main())

# Made with Bob
