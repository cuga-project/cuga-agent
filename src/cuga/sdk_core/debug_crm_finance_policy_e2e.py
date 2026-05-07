"""
Debug script for creating and testing CRM finance eligibility tool guard policy.

This script demonstrates:
1. Creating a CugaAgent with CRM tools from the registry
2. Adding a finance eligibility policy with tool guards
3. Generating examples and guard code for the policy
4. Testing policy enforcement with ToolGuardRuntime

Prerequisites:
- CRM API server must be running on port 8007
- Run: cuga start demo_crm (or just the CRM API server)

Note: This script uses an in-memory policy system for testing.
It does NOT persist policies to the running demo_crm server.
"""

import asyncio
import os
import tempfile
from cuga import CugaAgent
from cuga.backend.cuga_graph.policy.tool_guard import ToolGuardRuntime
from cuga.backend.cuga_graph.nodes.cuga_lite.combined_tool_provider import CombinedToolProvider

# Define policy to create
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


async def create_and_process_policy(agent):
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
        "policy": policy
    }


async def run_tests(tool_guard_runtime, agent):
    """Run test cases to validate policy enforcement."""
    print(f"\n{'='*60}")
    print("Step 2: Testing policy enforcement with ToolGuardRuntime")
    print(f"{'='*60}")
    
    print(f"\nRuntime initialized with guards for: {tool_guard_runtime.get_guarded_tools()}")
    
    test_cases = [
        {
            "name": "Test Case 1: Finance with Low Revenue (BLOCKED)",
            "args": {
                "name": "ACM22 Corporation",
                "website": "acm22corporation.com",
                "phone": "+1-555-1883",
                "address": "94 rue du Gue Jacquet",
                "city": "Chatou",
                "state": "Île-de-France",
                "country": "France",
                "region": "Europe",
                "annual_revenue": 50000,
                "employee_count": 88,
                "industry": "Finance"
            },
            "expected": "BLOCKED",
            "reason": "Finance industry with revenue $50,000 < $100,000"
        },
        {
            "name": "Test Case 2: Finance with High Revenue (ALLOWED)",
            "args": {
                "name": "Global Finance Corp",
                "website": "globalfinance.com",
                "phone": "+1-555-2000",
                "address": "123 Wall Street",
                "city": "New York",
                "state": "NY",
                "country": "USA",
                "region": "North America",
                "annual_revenue": 1500000,
                "employee_count": 250,
                "industry": "Finance"
            },
            "expected": "ALLOWED",
            "reason": "Finance industry with revenue $1,500,000 >= $100,000"
        },
        {
            "name": "Test Case 3: Non-Finance with Low Revenue (ALLOWED)",
            "args": {
                "name": "Small Law Firm",
                "website": "smalllawfirm.com",
                "phone": "+1-555-3000",
                "address": "456 Main Street",
                "city": "Boston",
                "state": "MA",
                "country": "USA",
                "region": "North America",
                "annual_revenue": 50000,
                "employee_count": 10,
                "industry": "Law"
            },
            "expected": "ALLOWED",
            "reason": "Law industry has no revenue restrictions"
        }
    ]
    
    results = []
    for test in test_cases:
        print(f"\n--- {test['name']} ---")
        print(f"Attempting: crm_create_account_accounts_post(")
        for k, v in test['args'].items():
            print(f"    {k}={repr(v)},")
        print(")")
        print(f"Expected: {test['expected']} ({test['reason']})")
        
        try:
            error = await tool_guard_runtime.guard_tool_call(
                app_name="crm_demo",
                function_name="crm_create_account_accounts_post",
                arguments=test["args"]
            )
            
            actual = "BLOCKED" if error else "ALLOWED"
            success = actual == test["expected"]
            
            if success:
                print(f"\n✅ SUCCESS: Tool call was correctly {actual}!")
                if error:
                    print(f"Error message: {error}")
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


async def main():
    """Main workflow for creating and testing CRM finance eligibility tool guard policy."""
    
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
        print("\nMake sure the CRM API server is running:")
        print("  cuga start demo_crm")
        print("  OR")
        print("  cd src/cuga/demo_tools/crm && python -m crm_api.main")
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
        
        # Get policy system
        policy_system = agent._policy_system
        if policy_system is None or policy_system.storage is None:
            raise ValueError("Policy system storage is not available")
        
        # Step 1: Create and process policy
        policy_data = await create_and_process_policy(agent)
        
        # Step 2: Initialize runtime and run tests
        print(f"\n{'='*60}")
        print("Initializing ToolGuardRuntime...")
        print(f"{'='*60}")
        
        tool_guard_runtime = ToolGuardRuntime(
            tool_provider=agent.tool_provider,
            policy_storage=policy_system.storage
        )
        await tool_guard_runtime.initialize()
        print("✅ ToolGuardRuntime initialized")
        
        results = await run_tests(tool_guard_runtime, agent)
        
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

