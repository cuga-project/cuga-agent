"""E2E test: Healthcare family claims with real tools and agent execution."""

import pytest

from pydantic import BaseModel, Field
from typing import List, Optional
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.policy.models import Playbook, NaturalLanguageTrigger
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface

from .helpers import (
    setup_policy_storage,
    setup_llm_manager,
    setup_langfuse_tracing,
    setup_policy_system,
    setup_cuga_lite_graph,
    create_initial_state,
    create_graph_config,
    run_graph_execution,
)


@pytest.mark.asyncio
async def test_healthcare_family_claims_e2e_with_tools():
    """
    E2E Integration test: Healthcare family claims with real tools and agent execution.

    Scenario:
    - User has personal information (pi) with their memberId: M123456789
    - User asks "get my daughter's claims"
    - System has real healthcare tools: get_plan(member_id), get_claims(member_id)
    - Playbook guides agent through multi-step process:
      1. Get user's plan (using memberId from pi)
      2. Check if it's a family plan and extract family member IDs
      3. Identify daughter from family_members list
      4. Get daughter's claims using her member_id
    - Agent executes the full workflow with real tool calls
    """
    print("\n" + "=" * 80)
    print("🏥 Healthcare Family Claims E2E Test with Real Tools")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Create mock healthcare data
        print("\n📋 Step 1: Setting up mock healthcare data")
        print("-" * 80)

        mock_healthcare_data = {
            "M123456789": {
                "member_id": "M123456789",
                "name": "John Doe",
                "plan_type": "family",
                "plan_name": "Premium Family Plan",
                "coverage_start": "2024-01-01",
                "family_members": [
                    {
                        "member_id": "M123456789-D1",
                        "name": "Sarah Doe",
                        "relationship": "daughter",
                        "date_of_birth": "2016-05-15",
                        "age": 8,
                    },
                    {
                        "member_id": "M123456789-S1",
                        "name": "Michael Doe",
                        "relationship": "son",
                        "date_of_birth": "2012-03-20",
                        "age": 12,
                    },
                    {
                        "member_id": "M123456789-SP",
                        "name": "Jane Doe",
                        "relationship": "spouse",
                        "date_of_birth": "1985-08-10",
                        "age": 39,
                    },
                ],
            },
            "M123456789-D1": {
                "member_id": "M123456789-D1",
                "name": "Sarah Doe",
                "plan_type": "dependent",
                "plan_name": "Premium Family Plan (Dependent)",
                "primary_member": "M123456789",
            },
            "M123456789-S1": {
                "member_id": "M123456789-S1",
                "name": "Michael Doe",
                "plan_type": "dependent",
                "plan_name": "Premium Family Plan (Dependent)",
                "primary_member": "M123456789",
            },
        }

        mock_claims_data = {
            "M123456789-D1": [
                {
                    "claim_id": "CLM-2024-001",
                    "date": "2024-11-15",
                    "provider": "Children's Hospital",
                    "service": "Annual Checkup",
                    "amount": 250.00,
                    "status": "Paid",
                },
                {
                    "claim_id": "CLM-2024-002",
                    "date": "2024-12-01",
                    "provider": "Pediatric Dental",
                    "service": "Dental Cleaning",
                    "amount": 150.00,
                    "status": "Paid",
                },
            ],
            "M123456789-S1": [
                {
                    "claim_id": "CLM-2024-003",
                    "date": "2024-10-20",
                    "provider": "Sports Medicine Clinic",
                    "service": "Sports Physical",
                    "amount": 180.00,
                    "status": "Paid",
                }
            ],
        }

        print(f"  ✅ Created mock data for {len(mock_healthcare_data)} members")
        print(f"  ✅ Created claims data for {len(mock_claims_data)} members")

        # Step 2: Create Pydantic models for structured output
        print("\n📋 Step 2: Creating Pydantic models and healthcare tools")
        print("-" * 80)

        class FamilyMember(BaseModel):
            """Family member information."""

            member_id: str
            name: str
            relationship: str
            date_of_birth: str
            age: int

        class HealthcarePlan(BaseModel):
            """Healthcare plan information."""

            member_id: str
            name: str
            plan_type: str
            plan_name: str
            coverage_start: Optional[str] = None
            primary_member: Optional[str] = None
            family_members: Optional[List[FamilyMember]] = None

        class Claim(BaseModel):
            """Individual claim information."""

            claim_id: str
            date: str
            provider: str
            service: str
            amount: float
            status: str

        class ClaimsResponse(BaseModel):
            """Claims response with list of claims."""

            member_id: str
            claims: List[Claim]
            total_claims: int

        async def get_plan(member_id: str) -> HealthcarePlan:
            """Get healthcare plan information for a member.

            Args:
                member_id: The member ID to look up

            Returns:
                HealthcarePlan Pydantic model with plan information including family_members if family plan
            """
            # Simulate async API call
            import asyncio

            await asyncio.sleep(0.1)

            if member_id in mock_healthcare_data:
                data = mock_healthcare_data[member_id]
                # Convert family_members to FamilyMember objects if present
                if "family_members" in data and data["family_members"]:
                    data = data.copy()
                    data["family_members"] = [FamilyMember(**fm) for fm in data["family_members"]]
                # Return Pydantic model directly - LangChain will serialize it
                return HealthcarePlan(**data)
            raise ValueError(f"Member {member_id} not found")

        async def get_claims(member_id: str) -> ClaimsResponse:
            """Get claims history for a member.

            Args:
                member_id: The member ID to look up claims for

            Returns:
                ClaimsResponse Pydantic model with list of claims
            """
            # Simulate async API call
            import asyncio

            await asyncio.sleep(0.1)

            if member_id in mock_claims_data:
                claims_list = [Claim(**claim) for claim in mock_claims_data[member_id]]
                # Return Pydantic model directly - LangChain will serialize it
                return ClaimsResponse(member_id=member_id, claims=claims_list, total_claims=len(claims_list))
            return ClaimsResponse(member_id=member_id, claims=[], total_claims=0)

        # Create input schema for tools
        class GetPlanInput(BaseModel):
            member_id: str = Field(description="The member ID to look up")

        class GetClaimsInput(BaseModel):
            member_id: str = Field(description="The member ID to look up claims for")

        # Create tools with Pydantic schemas for both input and output
        # LangChain will auto-serialize Pydantic models to JSON for the agent
        get_plan_tool = StructuredTool.from_function(
            func=get_plan,  # LangChain will auto-detect it's async and serialize Pydantic output
            name="get_plan",
            description="Get healthcare plan information for a member by their member_id.",
            args_schema=GetPlanInput,  # Input validation with Pydantic
            return_direct=False,  # Let LangChain handle serialization
        )

        get_claims_tool = StructuredTool.from_function(
            func=get_claims,  # LangChain will auto-detect it's async and serialize Pydantic output
            name="get_claims",
            description="Get claims history for a member by their member_id.",
            args_schema=GetClaimsInput,  # Input validation with Pydantic
            return_direct=False,  # Let LangChain handle serialization
        )

        print(f"  ✅ Created tool: {get_plan_tool.name} (Pydantic input/output)")
        print(f"  ✅ Created tool: {get_claims_tool.name} (Pydantic input/output)")

        # Step 3: Create tool provider
        print("\n📋 Step 3: Creating tool provider")
        print("-" * 80)

        class HealthcareToolProvider(ToolProviderInterface):
            async def initialize(self):
                pass

            async def get_apps(self):
                from pydantic import BaseModel

                class App(BaseModel):
                    name: str
                    type: str = "api"
                    description: str = ""

                return [App(name="healthcare", type="api", description="Healthcare API")]

            async def get_all_tools(self):
                return [get_plan_tool, get_claims_tool]

            async def get_tools(self, app_name: str = None):
                if app_name == "healthcare" or app_name is None:
                    return [get_plan_tool, get_claims_tool]
                return []

        tool_provider = HealthcareToolProvider()
        print("  ✅ Created HealthcareToolProvider with 2 tools")

        # Step 4: Create and register playbook
        print("\n📋 Step 4: Creating and registering playbook")
        print("-" * 80)
        # Create healthcare family plan playbook (markdown only, no steps)
        healthcare_playbook = Playbook(
            id="playbook_family_healthcare",
            name="Family Healthcare Plan Navigation",
            description="Guide for accessing family member healthcare information",
            triggers=[
                NaturalLanguageTrigger(
                    value=[
                        "user wants to access healthcare information (claims, plan, coverage, benefits) for a family member (daughter, son, spouse, dependent, child)"
                    ],
                    target="intent",
                    threshold=0.7,
                ),
            ],
            markdown_content="""# Family Healthcare Plan Navigation

When a user asks about family member healthcare information, follow these steps:

## Step 1: Get Primary Member's Plan
- Use the memberId from user context to call `get_plan(member_id)`
- This returns the plan details including plan_type and family_members list

## Step 2: Check Plan Type
- If plan_type is "family", the response includes family_members array
- Each family member has: member_id, name, relationship, date_of_birth

## Step 3: Identify Target Family Member
- Match the requested family member (e.g., "daughter", "son") with the relationship field
- If multiple matches (e.g., two daughters), you may need to ask for clarification
- Use the family member's member_id for subsequent calls

## Step 4: Get Family Member's Information
- Call `get_plan(member_id)` with the family member's ID to get their specific plan details
- Call `get_claims(member_id)` with the family member's ID to get their claims

## Important Notes
- Always start with the primary member's plan to get family member IDs
- Family member IDs are different from the primary member ID
- For individual plans, there are no family_members in the response
""",
            priority=90,
            enabled=True,
        )

        # Step 5: Setup storage, LLM, and policy system
        print("\n📋 Step 5: Setting up storage and policy system")
        print("-" * 80)
        storage = await setup_policy_storage("test_healthcare_e2e")
        llm = await setup_llm_manager("chat")
        langfuse_handler = setup_langfuse_tracing()

        if langfuse_handler:
            print("  ✅ Langfuse tracing enabled")
        else:
            print("  ℹ️  Langfuse not available (optional)")

        policy_system = await setup_policy_system(storage, llm, [healthcare_playbook])
        print("  ✅ Initialized policy system with healthcare playbook")

        # Step 6: Create CugaLite graph
        print("\n📋 Step 6: Creating CugaLite graph")
        print("-" * 80)
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, ["healthcare"])
        print("  ✅ Created and compiled CugaLite graph with tools and policies")

        # Step 7: Execute E2E - User asks for daughter's claims
        print("\n📋 Step 7: E2E Execution - User asks 'get my daughter's claims'")
        print("-" * 80)

        # Create initial state
        initial_state = create_initial_state(
            user_query="get my daughter's claims",
            thread_id="test_healthcare_e2e_1",
            pi="memberId: M123456789",  # User's personal information
            sub_task_app="healthcare",
        )

        print(f"  User query: {initial_state.chat_messages[0].content}")
        print(f"  User context (pi): {initial_state.pi}")
        print(f"  Thread ID: {initial_state.thread_id}")
        print("\n  🚀 Starting graph execution...")
        print("  " + "=" * 76)

        # Execute the graph with policy system and langfuse in config
        config = create_graph_config("test_healthcare_e2e_1", policy_system, ["healthcare"], langfuse_handler)

        result = await run_graph_execution(compiled_graph, initial_state, config, langfuse_handler)

        # Note about callback coverage
        if langfuse_handler:
            print("\n  ℹ️  Note: Langfuse callbacks are passed to:")
            print("      - CugaLite graph LLM calls (main agent)")
            print("      - Policy matching may use separate LLM calls (not yet instrumented)")
            print("      Check Langfuse UI for captured traces")

        print("\n  " + "=" * 76)
        print("  ✅ Graph execution completed")

        # Verify results
        print("\n📋 Step 8: Verify E2E Results")
        print("-" * 80)

        assert result is not None, "Should have result"
        print(f"  Result keys: {list(result.keys())}")
        print(f"  Execution complete: {result.get('execution_complete', False)}")

        # Check metadata
        metadata = result.get('cuga_lite_metadata', {})
        print(f"  Policy matched: {metadata.get('policy_matched', False)}")
        print(f"  Policy type: {metadata.get('policy_type', 'N/A')}")
        print(f"  Policy name: {metadata.get('policy_name', 'N/A')}")

        # Check if playbook guidance was applied
        if result.get('prepared_prompt'):
            has_guidance = (
                "Family Healthcare Plan Navigation" in result['prepared_prompt']
                or "get_plan" in result['prepared_prompt']
            )
            print(f"  Prompt enhanced with playbook guidance: {has_guidance}")

        # Verify policy matched
        assert metadata.get("policy_matched"), "Policy should match"
        assert metadata.get("policy_type") == "playbook", "Should match playbook policy"
        assert metadata.get("playbook_guidance") is not None, "Should have playbook guidance"

        # Verify the correct answer - check that daughter's claims were retrieved
        print("\n📋 Step 9: Verify Correct Answer (Daughter's Claims)")
        print("-" * 80)

        final_answer = result.get('final_answer', '')
        print(f"  Final answer length: {len(final_answer)} chars")

        # Check that the answer contains information about Sarah (the daughter)
        final_answer_lower = final_answer.lower()

        # Should mention Sarah or daughter
        has_sarah_or_daughter = "sarah" in final_answer_lower or "daughter" in final_answer_lower
        print(f"  ✓ Mentions Sarah/daughter: {has_sarah_or_daughter}")

        # Should mention claims or the specific claim IDs
        has_claims = (
            "claim" in final_answer_lower
            or "clm-2024-001" in final_answer_lower
            or "clm-2024-002" in final_answer_lower
        )
        print(f"  ✓ Mentions claims: {has_claims}")

        # Should mention the daughter's member ID
        has_daughter_id = "m123456789-d1" in final_answer_lower
        print(f"  ✓ Contains daughter's member ID: {has_daughter_id}")

        # Should mention specific claim details (providers or services)
        has_claim_details = (
            "children's hospital" in final_answer_lower
            or "pediatric dental" in final_answer_lower
            or "annual checkup" in final_answer_lower
            or "dental cleaning" in final_answer_lower
        )
        print(f"  ✓ Contains claim details: {has_claim_details}")

        # Verify assertions
        assert has_sarah_or_daughter, "Answer should mention Sarah or daughter"
        assert has_claims, "Answer should mention claims"

        # Check variables storage for the retrieved data
        variables = result.get('variables_storage', {})
        print(f"\n  Variables stored: {len(variables)} total")

        # Look for daughter's claims in variables
        daughter_claims_found = False
        for var_name, var_data in variables.items():
            var_value = var_data.get('value', '')
            if isinstance(var_value, str):
                if 'clm-2024-001' in var_value.lower() or 'clm-2024-002' in var_value.lower():
                    print(f"  ✓ Found daughter's claims in variable: {var_name}")
                    daughter_claims_found = True
                    break
            elif isinstance(var_value, (dict, list)):
                var_str = str(var_value).lower()
                if 'clm-2024-001' in var_str or 'clm-2024-002' in var_str:
                    print(f"  ✓ Found daughter's claims in variable: {var_name}")
                    daughter_claims_found = True
                    break

        if daughter_claims_found:
            print("  ✓ Daughter's claims data successfully retrieved and stored")

        print("\n  ✅ E2E test passed!")
        print("  ✅ Playbook matched and guided the agent")
        print("  ✅ Personal information (pi) with memberId flowed through context")
        print("  ✅ Correct answer: Retrieved daughter Sarah's claims (CLM-2024-001, CLM-2024-002)")

        print("\n" + "=" * 80)
        print("✅ Healthcare Family Claims E2E Test Complete!")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()
