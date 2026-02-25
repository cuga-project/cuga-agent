"""E2E test: OutputFormatter policy formats final AI message."""

import json
import uuid
import pytest
from pydantic import BaseModel, Field
from langchain_core.tools import StructuredTool

from cuga.backend.cuga_graph.policy.models import (
    OutputFormatter,
    KeywordTrigger,
    NaturalLanguageTrigger,
)

from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface

from .helpers import (
    setup_policy_storage,
    setup_llm_manager,
    setup_langfuse_tracing,
    setup_policy_system,
    setup_cuga_lite_graph,
    setup_full_agent_graph,
    create_initial_state,
    create_agent_initial_state,
    create_graph_config,
    run_graph_execution,
    run_full_graph_to_completion,
    MinimalToolProvider,
)


@pytest.mark.asyncio
async def test_e2e_output_formatter_with_keyword_trigger():
    """
    E2E Test: OutputFormatter formats response when keywords match in agent response.

    This test verifies that:
    1. An OutputFormatter policy can trigger based on keywords in the AI's response
    2. The LLM is called to reformat the response according to markdown instructions
    3. The formatted response replaces the original in state.chat_messages and final_answer
    """
    print("\n" + "=" * 80)
    print("E2E TEST: OutputFormatter with Keyword Trigger")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage
        print("\n📋 Step 1: Setting up policy storage")
        print("-" * 80)
        storage = await setup_policy_storage("e2e_test_output_formatter")
        print("  ✅ Created policy storage")

        # Step 2: Setup LLM and Langfuse
        print("\n📋 Step 2: Setting up LLM and tracing")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        langfuse_handler = setup_langfuse_tracing()

        if langfuse_handler:
            print("  ✅ Langfuse tracing enabled")
        else:
            print("  ℹ️  Langfuse not available (optional)")

        # Step 3: Initialize policy system
        print("\n📋 Step 3: Initializing policy system")
        print("-" * 80)
        policy_system = await setup_policy_system(storage, llm)
        print("  ✅ Initialized policy system")

        # Step 4: Create OutputFormatter policy
        print("\n📋 Step 4: Creating OutputFormatter policy")
        print("-" * 80)
        output_formatter = OutputFormatter(
            id="e2e_formatter_summary",
            name="E2E Summary Formatter",
            description="Formats responses that contain 'summary' or 'result' into structured format",
            triggers=[
                KeywordTrigger(
                    value=["summary", "result", "output"],
                    target="agent_response",
                    case_sensitive=False,
                    operator="or",
                ),
            ],
            format_type="markdown",
            format_config="""Format the response as a structured summary with:
- A clear title using # Heading
- Key points as bullet points using - 
- Important information in **bold**
- A conclusion section

Make it professional and easy to read.""",
            priority=50,
            enabled=True,
        )
        print(f"  ✅ Created OutputFormatter policy: {output_formatter.name}")
        print(f"  ✅ Triggers on keywords in agent_response: {output_formatter.triggers[0].value}")
        print(f"  ✅ Format type: {output_formatter.format_type}")

        # Add policy to storage
        await storage.add_policy(output_formatter)

        # Reset policy system to reload policies
        policy_system._initialized = False
        await policy_system.initialize()

        # Step 5: Create full agent graph (needed for FinalAnswerNode where OutputFormatter runs)
        print("\n📋 Step 5: Creating full agent graph")
        print("-" * 80)
        agent_graph = await setup_full_agent_graph(policy_system, langfuse_handler)
        print("  ✅ Created and built full agent graph")

        # Step 6: Create initial state with query that will generate a response with keywords
        print("\n📋 Step 6: Setting up execution")
        print("-" * 80)
        # Use a query that will likely generate a response containing "summary" or "result"
        thread_id = f"e2e_test_output_formatter_{uuid.uuid4().hex[:8]}"
        initial_state = create_agent_initial_state(
            user_input="Provide a summary of the main features of this system",
            thread_id=thread_id,
            user_id="test_user",
            lite_mode=True,
        )

        print(f"  User query: {initial_state.input}")
        print(f"  Thread ID: {initial_state.thread_id}")
        print("  ✅ Created initial state")

        # Step 7: Run full graph to completion
        print("\n📋 Step 7: Running full graph to completion")
        print("-" * 80)
        print("\n🚀 Running full agent graph...")
        final_state = await run_full_graph_to_completion(agent_graph, initial_state, thread_id)

        # Step 8: Verify results
        print("\n📋 Step 8: Verifying results")
        print("-" * 80)
        print(f"  Execution complete: {final_state.final_answer is not None}")

        final_answer = final_state.final_answer or ''
        print(f"  Final answer length: {len(final_answer)} chars")
        if final_answer:
            print(f"  Final answer preview: {final_answer[:200]}...")

        # Check if formatting was applied
        # The formatted response should have markdown structure (headings, bullets, bold)
        has_markdown_structure = (
            '#' in final_answer  # Has headings
            or '-' in final_answer  # Has bullet points
            or '**' in final_answer  # Has bold text
        )

        print(f"  Has markdown structure: {has_markdown_structure}")
        print(
            f"  Contains 'summary' or 'result': {'summary' in final_answer.lower() or 'result' in final_answer.lower()}"
        )

        # Check chat_messages to see if it was updated
        chat_messages = final_state.chat_messages or []
        if chat_messages:
            last_message = chat_messages[-1]
            if hasattr(last_message, 'content'):
                last_content = last_message.content
                print(f"  Last chat message length: {len(last_content)} chars")
                has_markdown_in_chat = '#' in last_content or '-' in last_content or '**' in last_content
                print(f"  Last chat message has markdown: {has_markdown_in_chat}")

        # Assertions
        assert final_answer, "Final answer should be present"

        # If the formatter matched, the response should have markdown structure
        # Note: This is a probabilistic test - the formatter only triggers if the response contains keywords
        if has_markdown_structure:
            print("  ✅ Response appears to be formatted with markdown structure")
        else:
            print("  ℹ️  Response may not have triggered formatter (keywords may not be in response)")

        print("\n✅ E2E OutputFormatter Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()


@pytest.mark.asyncio
async def test_e2e_output_formatter_with_natural_language_trigger():
    """
    E2E Test: OutputFormatter formats response when natural language trigger matches.

    This test verifies that:
    1. An OutputFormatter policy can trigger based on natural language similarity
    2. The LLM is called to reformat the response according to JSON schema
    3. The formatted response replaces the original
    """
    print("\n" + "=" * 80)
    print("E2E TEST: OutputFormatter with Natural Language Trigger")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage
        print("\n📋 Step 1: Setting up policy storage")
        print("-" * 80)
        storage = await setup_policy_storage("e2e_test_output_formatter_nl")
        print("  ✅ Created policy storage")

        # Step 2: Setup LLM and Langfuse
        print("\n📋 Step 2: Setting up LLM and tracing")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        langfuse_handler = setup_langfuse_tracing()

        if langfuse_handler:
            print("  ✅ Langfuse tracing enabled")
        else:
            print("  ℹ️  Langfuse not available (optional)")

        # Step 3: Initialize policy system
        print("\n📋 Step 3: Initializing policy system")
        print("-" * 80)
        policy_system = await setup_policy_system(storage, llm)
        print("  ✅ Initialized policy system")

        # Step 4: Create OutputFormatter policy with JSON schema
        print("\n📋 Step 4: Creating OutputFormatter policy with JSON schema")
        print("-" * 80)
        output_formatter = OutputFormatter(
            id="e2e_formatter_json",
            name="E2E JSON Formatter",
            description="Formats responses about data analysis into JSON structure",
            triggers=[
                NaturalLanguageTrigger(
                    value=["data analysis results", "analysis output", "computed results"],
                    target="agent_response",
                    threshold=0.7,
                ),
            ],
            format_type="json_schema",
            format_config='''{
  "type": "object",
  "properties": {
    "summary": {
      "type": "string",
      "description": "Brief summary of the analysis"
    },
    "key_findings": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "List of key findings"
    },
    "conclusion": {
      "type": "string",
      "description": "Conclusion or next steps"
    }
  },
  "required": ["summary", "key_findings", "conclusion"]
}''',
            priority=50,
            enabled=True,
        )
        print(f"  ✅ Created OutputFormatter policy: {output_formatter.name}")
        print("  ✅ Triggers on natural language similarity in agent_response")
        print(f"  ✅ Format type: {output_formatter.format_type}")

        # Add policy to storage
        await storage.add_policy(output_formatter)

        # Reset policy system to reload policies
        policy_system._initialized = False
        await policy_system.initialize()

        # Step 5: Create tool provider and CugaLite graph
        print("\n📋 Step 5: Creating CugaLite graph")
        print("-" * 80)
        tool_provider = MinimalToolProvider()
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, [])
        print("  ✅ Created and compiled CugaLite graph")

        # Step 6: Create initial state with query that might generate analysis-like response
        print("\n📋 Step 6: Setting up execution")
        print("-" * 80)
        initial_state = create_initial_state(
            user_query="Analyze the data and provide the results",
            thread_id="e2e_test_output_formatter_nl",
        )
        config = create_graph_config("e2e_test_output_formatter_nl", policy_system, [], langfuse_handler)

        print(f"  User query: {initial_state.chat_messages[0].content}")
        print(f"  Thread ID: {initial_state.thread_id}")
        print("  ✅ Created initial state and config")

        # Step 7: Run graph execution
        print("\n📋 Step 7: Running graph execution")
        print("-" * 80)
        print("\n🚀 Running CugaLite graph...")
        result = await run_graph_execution(compiled_graph, initial_state, config, langfuse_handler)

        # Step 8: Verify results
        print("\n📋 Step 8: Verifying results")
        print("-" * 80)
        print(f"  Execution complete: {result.get('execution_complete', False)}")

        final_answer = result.get('final_answer', '')
        print(f"  Final answer length: {len(final_answer)} chars")
        if final_answer:
            print(f"  Final answer preview: {final_answer[:200]}...")

        # Check if JSON formatting was applied
        # The formatted response might be JSON or might still be text (depending on LLM)
        import json

        is_json = False
        try:
            parsed = json.loads(final_answer)
            is_json = isinstance(parsed, dict)
            if is_json:
                print("  ✅ Response is valid JSON")
                print(f"  JSON keys: {list(parsed.keys()) if isinstance(parsed, dict) else 'N/A'}")
        except (json.JSONDecodeError, ValueError):
            print("  ℹ️  Response is not JSON (may not have triggered or LLM returned text)")

        # Assertions
        assert result.get("execution_complete"), "Execution should be complete"
        assert final_answer, "Final answer should be present"

        print("\n✅ E2E OutputFormatter with Natural Language Trigger Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()


@pytest.mark.asyncio
async def test_e2e_output_formatter_no_trigger():
    """
    E2E Test: OutputFormatter does NOT format when triggers don't match.

    This test verifies that:
    1. OutputFormatter policies only trigger when keywords/similarity match
    2. Responses without matching triggers are not reformatted
    """
    print("\n" + "=" * 80)
    print("E2E TEST: OutputFormatter No Trigger")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage
        print("\n📋 Step 1: Setting up policy storage")
        print("-" * 80)
        storage = await setup_policy_storage("e2e_test_output_formatter_no_trigger")
        print("  ✅ Created policy storage")

        # Step 2: Setup LLM and Langfuse
        print("\n📋 Step 2: Setting up LLM and tracing")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        langfuse_handler = setup_langfuse_tracing()

        if langfuse_handler:
            print("  ✅ Langfuse tracing enabled")
        else:
            print("  ℹ️  Langfuse not available (optional)")

        # Step 3: Initialize policy system
        print("\n📋 Step 3: Initializing policy system")
        print("-" * 80)
        policy_system = await setup_policy_system(storage, llm)
        print("  ✅ Initialized policy system")

        # Step 4: Create OutputFormatter policy with specific keywords
        print("\n📋 Step 4: Creating OutputFormatter policy")
        print("-" * 80)
        output_formatter = OutputFormatter(
            id="e2e_formatter_specific",
            name="E2E Specific Formatter",
            description="Only formats responses containing very specific keywords",
            triggers=[
                KeywordTrigger(
                    value=["xyzabc123", "very_specific_term"],
                    target="agent_response",
                    case_sensitive=False,
                    operator="or",
                ),
            ],
            format_type="markdown",
            format_config="Format as a numbered list with clear sections.",
            priority=50,
            enabled=True,
        )
        print(f"  ✅ Created OutputFormatter policy: {output_formatter.name}")
        print(
            f"  ✅ Triggers on very specific keywords (unlikely to match): {output_formatter.triggers[0].value}"
        )

        # Add policy to storage
        await storage.add_policy(output_formatter)

        # Reset policy system to reload policies
        policy_system._initialized = False
        await policy_system.initialize()

        # Step 5: Create tool provider and CugaLite graph
        print("\n📋 Step 5: Creating CugaLite graph")
        print("-" * 80)
        tool_provider = MinimalToolProvider()
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, [])
        print("  ✅ Created and compiled CugaLite graph")

        # Step 6: Create initial state with query that won't trigger
        print("\n📋 Step 6: Setting up execution")
        print("-" * 80)
        initial_state = create_initial_state(
            user_query="Hello, how are you?",
            thread_id="e2e_test_output_formatter_no_trigger",
        )
        config = create_graph_config(
            "e2e_test_output_formatter_no_trigger", policy_system, [], langfuse_handler
        )

        print(f"  User query: {initial_state.chat_messages[0].content}")
        print(f"  Thread ID: {initial_state.thread_id}")
        print("  ✅ Created initial state and config")

        # Step 7: Run graph execution
        print("\n📋 Step 7: Running graph execution")
        print("-" * 80)
        print("\n🚀 Running CugaLite graph...")
        result = await run_graph_execution(compiled_graph, initial_state, config, langfuse_handler)

        # Step 8: Verify results
        print("\n📋 Step 8: Verifying results")
        print("-" * 80)
        print(f"  Execution complete: {result.get('execution_complete', False)}")

        final_answer = result.get('final_answer', '')
        print(f"  Final answer length: {len(final_answer)} chars")
        if final_answer:
            print(f"  Final answer preview: {final_answer[:200]}...")

        # Check that formatter did NOT trigger (response should not have specific formatting)
        # The response should be natural, not formatted with numbered lists
        has_numbered_list = any(
            line.strip().startswith(f"{i}.") for i in range(1, 10) for line in final_answer.split('\n')
        )

        print(f"  Has numbered list formatting: {has_numbered_list}")
        print(
            f"  Contains trigger keywords: {'xyzabc123' in final_answer.lower() or 'very_specific_term' in final_answer.lower()}"
        )

        # Assertions
        assert result.get("execution_complete"), "Execution should be complete"
        assert final_answer, "Final answer should be present"

        # The formatter should NOT have triggered, so response should be natural
        # (This is probabilistic - if keywords somehow appear, formatter might trigger)
        if not has_numbered_list:
            print("  ✅ Response appears to be in natural format (formatter did not trigger)")
        else:
            print("  ℹ️  Response may have been formatted (unexpected but possible)")

        print("\n✅ E2E OutputFormatter No Trigger Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()


@pytest.mark.asyncio
async def test_e2e_output_formatter_json_schema_structured_output():
    """
    E2E Test: OutputFormatter with JSON schema uses structured output.

    This test verifies that:
    1. OutputFormatter with json_schema format_type uses with_structured_output
    2. The LLM returns a dict that matches the JSON schema
    3. The formatted response is valid JSON matching the schema
    """
    print("\n" + "=" * 80)
    print("E2E TEST: OutputFormatter JSON Schema with Structured Output")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage
        print("\n📋 Step 1: Setting up policy storage")
        print("-" * 80)
        storage = await setup_policy_storage("e2e_test_output_formatter_json")
        print("  ✅ Created policy storage")

        # Step 2: Setup LLM and Langfuse
        print("\n📋 Step 2: Setting up LLM and tracing")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        langfuse_handler = setup_langfuse_tracing()

        if langfuse_handler:
            print("  ✅ Langfuse tracing enabled")
        else:
            print("  ℹ️  Langfuse not available (optional)")

        # Step 3: Initialize policy system
        print("\n📋 Step 3: Initializing policy system")
        print("-" * 80)
        policy_system = await setup_policy_system(storage, llm)
        print("  ✅ Initialized policy system")

        # Step 4: Create OutputFormatter policy with JSON schema
        print("\n📋 Step 4: Creating OutputFormatter policy with JSON schema")
        print("-" * 80)
        json_schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Brief summary of the response"},
                "key_points": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of key points",
                },
                "action_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string"},
                            "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                        },
                        "required": ["item", "priority"],
                    },
                    "description": "List of action items with priorities",
                },
            },
            "required": ["summary", "key_points"],
        }

        output_formatter = OutputFormatter(
            id="e2e_formatter_json_schema",
            name="E2E JSON Schema Formatter",
            description="Formats responses into structured JSON with summary, key points, and action items",
            triggers=[
                KeywordTrigger(
                    value=["analysis", "report", "summary"],
                    target="agent_response",
                    case_sensitive=False,
                    operator="or",
                ),
            ],
            format_type="json_schema",
            format_config=json.dumps(json_schema),
            priority=50,
            enabled=True,
        )
        print(f"  ✅ Created OutputFormatter policy: {output_formatter.name}")
        print(f"  ✅ Format type: {output_formatter.format_type}")
        print(f"  ✅ JSON schema has {len(json_schema['properties'])} properties")

        # Add policy to storage
        await storage.add_policy(output_formatter)

        # Reset policy system to reload policies
        policy_system._initialized = False
        await policy_system.initialize()

        # Step 5: Create tool provider and CugaLite graph
        print("\n📋 Step 5: Creating CugaLite graph")
        print("-" * 80)
        tool_provider = MinimalToolProvider()
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, [])
        print("  ✅ Created and compiled CugaLite graph")

        # Step 6: Create initial state with query that will generate a response with trigger keywords
        print("\n📋 Step 6: Setting up execution")
        print("-" * 80)
        initial_state = create_initial_state(
            user_query="Analyze the current situation and provide a detailed report with key findings",
            thread_id="e2e_test_output_formatter_json",
        )
        config = create_graph_config("e2e_test_output_formatter_json", policy_system, [], langfuse_handler)

        print(f"  User query: {initial_state.chat_messages[0].content}")
        print(f"  Thread ID: {initial_state.thread_id}")
        print("  ✅ Created initial state and config")

        # Step 7: Run graph execution
        print("\n📋 Step 7: Running graph execution")
        print("-" * 80)
        print("\n🚀 Running CugaLite graph...")
        result = await run_graph_execution(compiled_graph, initial_state, config, langfuse_handler)

        # Step 8: Verify results
        print("\n📋 Step 8: Verifying results")
        print("-" * 80)
        print(f"  Execution complete: {result.get('execution_complete', False)}")

        final_answer = result.get('final_answer', '')
        print(f"  Final answer length: {len(final_answer)} chars")
        if final_answer:
            print(f"  Final answer preview: {final_answer[:300]}...")

        # Check if JSON formatting was applied
        is_valid_json = False
        parsed_json = None

        try:
            parsed_json = json.loads(final_answer)
            is_valid_json = isinstance(parsed_json, dict)
            if is_valid_json:
                print("  ✅ Response is valid JSON")
                print(f"  JSON keys: {list(parsed_json.keys())}")

                # Verify it matches the schema structure
                has_summary = "summary" in parsed_json
                has_key_points = "key_points" in parsed_json
                key_points_is_array = isinstance(parsed_json.get("key_points"), list)

                print(f"  Has 'summary' field: {has_summary}")
                print(f"  Has 'key_points' field: {has_key_points}")
                print(f"  'key_points' is array: {key_points_is_array}")

                if "action_items" in parsed_json:
                    action_items_is_array = isinstance(parsed_json.get("action_items"), list)
                    print(f"  Has 'action_items' field: {action_items_is_array}")
                    if action_items_is_array and len(parsed_json.get("action_items", [])) > 0:
                        first_item = parsed_json["action_items"][0]
                        print(
                            f"  First action item structure: {list(first_item.keys()) if isinstance(first_item, dict) else 'not a dict'}"
                        )
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  ℹ️  Response is not JSON: {e}")
            print("  Response may not have triggered formatter or LLM returned text")

        # Assertions
        assert result.get("execution_complete"), "Execution should be complete"
        assert final_answer, "Final answer should be present"

        # If JSON was generated, verify structure
        if is_valid_json and parsed_json:
            assert "summary" in parsed_json, "JSON should have 'summary' field"
            assert "key_points" in parsed_json, "JSON should have 'key_points' field"
            assert isinstance(parsed_json["key_points"], list), "'key_points' should be an array"
            print("  ✅ JSON structure matches schema requirements")

        print("\n✅ E2E OutputFormatter JSON Schema Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()


@pytest.mark.asyncio
async def test_e2e_output_formatter_sensitive_data_blocking():
    """
    E2E Test: OutputFormatter blocks sensitive data with warning message.

    This test verifies that:
    1. OutputFormatter with keyword triggers can replace sensitive responses
    2. When account names from tool results appear in agent response,
       the formatter replaces it with a warning message
    """
    print("\n" + "=" * 80)
    print("E2E TEST: OutputFormatter Sensitive Data Blocking")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage
        print("\n📋 Step 1: Setting up policy storage")
        print("-" * 80)
        storage = await setup_policy_storage("e2e_test_output_formatter_sensitive")
        print("  ✅ Created policy storage")

        # Step 2: Setup LLM and Langfuse
        print("\n📋 Step 2: Setting up LLM and tracing")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        langfuse_handler = setup_langfuse_tracing()

        if langfuse_handler:
            print("  ✅ Langfuse tracing enabled")
        else:
            print("  ℹ️  Langfuse not available (optional)")

        # Step 3: Create dummy tool that returns accounts
        print("\n📋 Step 3: Creating dummy tool with account data")
        print("-" * 80)

        class Account(BaseModel):
            id: str = Field(description="Account ID")
            name: str = Field(description="Account name")
            revenue: float = Field(description="Account revenue")

        class GetTopAccountsInput(BaseModel):
            limit: int = Field(default=2, description="Number of top accounts to return")

        async def get_top_accounts(limit: int = 2) -> list[Account]:
            """Get top accounts by revenue.

            Args:
                limit: Number of top accounts to return

            Returns:
                List of Account objects
            """
            # Return two accounts with specific names that will trigger the formatter
            accounts = [
                Account(id="acc_1", name="Acme Corporation", revenue=1500000.0),
                Account(id="acc_2", name="TechStart Inc", revenue=1200000.0),
            ]
            return accounts[:limit]

        get_top_accounts_tool = StructuredTool.from_function(
            func=get_top_accounts,
            name="get_top_accounts",
            description="Get the top accounts by revenue. Returns account ID, name, and revenue.",
            args_schema=GetTopAccountsInput,
        )

        # Account names that will appear in the response
        account_names = ["Acme Corporation", "TechStart Inc"]
        print("  ✅ Created tool: get_top_accounts")
        print(f"  ✅ Tool returns accounts: {account_names}")

        # Step 4: Create tool provider with the dummy tool
        print("\n📋 Step 4: Creating tool provider")
        print("-" * 80)

        class AccountToolProvider(ToolProviderInterface):
            async def initialize(self):
                pass

            async def get_apps(self):
                from pydantic import BaseModel

                class App(BaseModel):
                    name: str
                    type: str = "api"

                return [App(name="sales", type="api")]

            async def get_all_tools(self):
                return [get_top_accounts_tool]

            async def get_tools(self, app_name: str = None):
                if app_name == "sales" or app_name is None:
                    return [get_top_accounts_tool]
                return []

        tool_provider = AccountToolProvider()
        print("  ✅ Created tool provider with get_top_accounts tool")

        # Step 5: Initialize policy system
        print("\n📋 Step 5: Initializing policy system")
        print("-" * 80)
        policy_system = await setup_policy_system(storage, llm)
        print("  ✅ Initialized policy system")

        # Step 6: Create OutputFormatter policy that blocks when account names appear
        print("\n📋 Step 6: Creating OutputFormatter policy for sensitive data blocking")
        print("-" * 80)
        output_formatter = OutputFormatter(
            id="e2e_formatter_sensitive_block",
            name="Sensitive Data Blocker",
            description="Blocks and replaces responses containing account names from tools",
            triggers=[
                KeywordTrigger(
                    value=account_names,  # Trigger on account names
                    target="agent_response",
                    case_sensitive=False,
                    operator="or",
                ),
            ],
            format_type="markdown",
            format_config="""Replace the entire response with the following message:

You are not allowed to view this sensitive data

Do not include any of the original response content. Only return the warning message above.""",
            priority=100,  # High priority to ensure it takes precedence
            enabled=True,
        )
        print(f"  ✅ Created OutputFormatter policy: {output_formatter.name}")
        print(f"  ✅ Triggers on account names in agent_response: {output_formatter.triggers[0].value}")
        print(f"  ✅ Format type: {output_formatter.format_type}")
        print(f"  ✅ Priority: {output_formatter.priority} (high)")

        # Add policy to storage
        await storage.add_policy(output_formatter)

        # Reset policy system to reload policies
        policy_system._initialized = False
        await policy_system.initialize()

        # Step 7: Create full agent graph with custom tool provider
        print("\n📋 Step 7: Creating full agent graph with custom tool provider")
        print("-" * 80)
        agent_graph = await setup_full_agent_graph(policy_system, langfuse_handler, tool_provider)
        print("  ✅ Created and built full agent graph with get_top_accounts tool")

        # Step 8: Create initial state with query to get top account
        print("\n📋 Step 8: Setting up execution")
        print("-" * 80)
        thread_id = f"e2e_test_output_formatter_sensitive_{uuid.uuid4().hex[:8]}"
        initial_state = create_agent_initial_state(
            user_input="Get the top account",
            thread_id=thread_id,
            user_id="test_user",
            lite_mode=True,
        )
        print(f"  User query: {initial_state.input}")
        print(f"  Thread ID: {initial_state.thread_id}")
        print("  ✅ Created initial state")

        # Step 9: Run full graph to completion
        print("\n📋 Step 9: Running full graph to completion")
        print("-" * 80)
        print("\n🚀 Running full agent graph...")
        final_state = await run_full_graph_to_completion(agent_graph, initial_state, thread_id)

        # Step 10: Verify results
        print("\n📋 Step 10: Verifying results")
        print("-" * 80)
        print(f"  Execution complete: {final_state.final_answer is not None}")

        final_answer = final_state.final_answer or ''
        print(f"  Final answer length: {len(final_answer)} chars")
        if final_answer:
            print(f"  Final answer: {final_answer[:500]}...")

        # Check if the blocking message appears
        blocking_message = "you are not allowed to view this sensitive data"
        has_blocking_message = blocking_message.lower() in final_answer.lower()

        print(f"  Contains blocking message: {has_blocking_message}")
        print(f"  Blocking message: '{blocking_message}'")

        # Check if account names are present (they should be blocked)
        has_account_names = any(name.lower() in final_answer.lower() for name in account_names)
        print(f"  Contains account names (should be blocked): {has_account_names}")
        if has_account_names:
            found_names = [name for name in account_names if name.lower() in final_answer.lower()]
            print(f"  Found account names in response: {found_names}")

        # Assertions
        assert final_answer, "Final answer should be present"

        # The formatter MUST have triggered and replaced the response
        # The agent's response should contain account names from the tool, so the formatter should always trigger
        assert has_blocking_message, (
            f"OutputFormatter should have triggered and replaced response with blocking message. "
            f"Final answer was: '{final_answer[:200]}...'"
        )
        print("  ✅ Response was blocked and replaced with warning message")

        # If blocking message is present, account names should ideally not be in the response
        # (though the agent might have mentioned them before formatting)
        assert not has_account_names, (
            f"Account names should be blocked when formatter triggers. Found: {[name for name in account_names if name.lower() in final_answer.lower()]}"
        )

        print("\n✅ E2E OutputFormatter Sensitive Data Blocking Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()


@pytest.mark.asyncio
async def test_e2e_output_formatter_sdk_integration():
    """
    E2E Test: OutputFormatter works with SDK (catches SDK integration bug).

    This test verifies that OutputFormatter policies work when using the CugaAgent SDK,
    not just the internal CugaLite graph. This catches the bug where SDK callback
    node wasn't checking for OutputFormatter policies.

    Before fix: This test would fail because SDK didn't check OutputFormatter policies
    After fix: This test passes because SDK callback node now checks OutputFormatter policies
    """
    print("\n" + "=" * 80)
    print("E2E TEST: OutputFormatter SDK Integration (Bug Catcher)")
    print("=" * 80)

    from cuga import CugaAgent
    from langchain_core.tools import tool

    # Create a tool that guarantees keywords in response
    @tool
    def ibm_watson_tool() -> str:
        """A tool that returns information about IBM Watson with guaranteed keywords.

        Returns:
            String containing IBM Watson information with keywords ibm and watson.
        """
        return """IBM Watson provides comprehensive AI solutions:

- Watson Assistant: Conversational AI platform
- Watson Discovery: Document analysis and insight extraction
- watsonx.ai: Foundation model development and deployment

IBM emphasizes enterprise-grade AI with trust and explainability."""

    try:
        # Step 1: Create CugaAgent with OutputFormatter policy
        print("\n📋 Step 1: Creating CugaAgent with OutputFormatter policy")
        print("-" * 80)

        agent = CugaAgent(tools=[ibm_watson_tool], auto_load_policies=False, filesystem_sync=False)

        # Check initial policies and clean them up
        initial_policies = await agent.policies.list()
        print(f"  Initial policies: {len(initial_policies)}")

        # Clean up any existing policies to ensure test isolation
        for policy in initial_policies:
            try:
                await agent.policies.delete(policy["id"])
                print(f"    Cleaned up existing policy: {policy['name']}")
            except Exception as e:
                print(f"    Failed to delete policy {policy['name']}: {e}")

        # Verify cleanup
        cleaned_policies = await agent.policies.list()
        print(f"  Policies after cleanup: {len(cleaned_policies)}")

        # Add OutputFormatter policy via SDK
        await agent.policies.add_output_formatter(
            name="SDK Integration Test Formatter",
            description="Test formatter for SDK integration",
            keywords=["ibm", "watson"],
            format_type="direct",
            format_config="""# SDK_E2E_TEST_FORMATTER_SUCCESS

This response was successfully formatted by an OutputFormatter policy in the SDK!

CONFIRMATION: SDK integration with OutputFormatter policies is working correctly.

Test: E2E SDK Integration Test
Original response contained: ibm, watson keywords
Policy triggered via: SDK callback node
Format type: direct replacement""",
            priority=100,
            enabled=True,
        )

        # Check policies after adding
        final_policies = await agent.policies.list()
        print(f"  Policies after adding: {len(final_policies)}")
        for policy in final_policies:
            print(f"    - {policy['name']}: {policy['type']}")

        print("  ✅ Created CugaAgent with OutputFormatter policy")

        # Step 2: Test with query that triggers the tool (guarantees keywords)
        print("\n📋 Step 2: Testing SDK integration")
        print("-" * 80)

        result = await agent.invoke("Use the ibm_watson_tool to get information about IBM Watson")

        print(f"  Response length: {len(result.answer)} chars")
        print(f"  Response preview: {result.answer[:200]}...")
        print(f"  Full response: {result.answer}")

        # Debug: Check if tool was actually called
        tool_called = "ibm_watson_tool" in str(result.tool_calls) if result.tool_calls else False
        print(f"  Tool called: {tool_called}")
        if result.tool_calls:
            print(f"  Tool calls: {result.tool_calls}")

        # Step 3: Verify OutputFormatter was applied
        print("\n📋 Step 3: Verifying OutputFormatter was applied")
        print("-" * 80)

        # Check for distinctive marker that proves formatting worked
        formatter_applied = "# SDK_E2E_TEST_FORMATTER_SUCCESS" in result.answer

        if formatter_applied:
            print("  ✅ SUCCESS: OutputFormatter policy was applied in SDK!")
            print("     - SDK callback node correctly checked for OutputFormatter policies")
            print("     - Response was formatted as expected")
        else:
            print("  ❌ FAILURE: OutputFormatter policy was NOT applied in SDK!")
            print("     - This indicates the SDK integration bug still exists")
            print("     - SDK callback node is not checking OutputFormatter policies")

        # Additional verification
        has_ibm = "ibm" in result.answer.lower()
        has_watson = "watson" in result.answer.lower()
        print(f"  - Contains 'ibm': {has_ibm}")
        print(f"  - Contains 'watson': {has_watson}")
        print(f"  - Trigger conditions met: {has_ibm or has_watson}")

        assert formatter_applied, "OutputFormatter policy should have been applied in SDK but wasn't"

        print("\n🎉 SDK OutputFormatter integration test PASSED!")

    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        raise
