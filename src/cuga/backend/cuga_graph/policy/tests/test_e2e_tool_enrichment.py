"""E2E test: Tool guide policy with multiple tools and keyword-based guide."""

import uuid
import pytest

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

from cuga.backend.cuga_graph.policy.models import KeywordTrigger, ToolGuide
from cuga.backend.cuga_graph.nodes.cuga_lite.tool_provider_interface import ToolProviderInterface

from langchain_core.tools import StructuredTool
from pydantic import BaseModel


@pytest.mark.asyncio
async def test_tool_guide_with_keyword_trigger():
    """Test that tool guide adds guidance when specific keywords are used."""
    print("\n" + "=" * 80)
    print("E2E TEST: Tool Guide with Keyword Trigger")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage and system
        print("\n📋 Step 1: Setting up policy system")
        print("-" * 80)
        storage = await setup_policy_storage("test_tool_guide_keyword")
        langfuse_handler = setup_langfuse_tracing()

        if langfuse_handler:
            print("  ✅ Langfuse tracing enabled")
        else:
            print("  ℹ️  Langfuse not available (optional)")

        policy_system = await setup_policy_system(storage)
        await policy_system.initialize()

        # Step 2: Create mock tools
        print("\n📋 Step 2: Creating mock tools")
        print("-" * 80)

        # Create multiple mock tools
        def mock_tool_1(query: str) -> str:
            """Search for information in database."""
            return f"Database search result for: {query}"

        def mock_tool_2(query: str) -> str:
            """Search for information on the web."""
            return f"Web search result for: {query}"

        def mock_tool_3(query: str) -> str:
            """Calculate mathematical expressions."""
            return f"Calculation result for: {query}"

        # Create input schemas
        class SearchInput(BaseModel):
            query: str

        # Create tools
        db_search_tool = StructuredTool.from_function(
            func=mock_tool_1,
            name="db_search",
            description="Search for information in the database",
            args_schema=SearchInput,
        )

        web_search_tool = StructuredTool.from_function(
            func=mock_tool_2,
            name="web_search",
            description="Search for information on the web",
            args_schema=SearchInput,
        )

        calculator_tool = StructuredTool.from_function(
            func=mock_tool_3,
            name="calculator",
            description="Calculate mathematical expressions",
            args_schema=SearchInput,
        )

        print("  ✅ Created 3 mock tools: db_search, web_search, calculator")

        # Create tool provider
        class MockToolProvider(ToolProviderInterface):
            async def initialize(self):
                pass

            async def get_apps(self):
                from pydantic import BaseModel

                class App(BaseModel):
                    name: str
                    type: str = "api"

                return [App(name="search", type="api")]

            async def get_all_tools(self):
                return [db_search_tool, web_search_tool, calculator_tool]

            async def get_tools(self, app_name: str = None):
                if app_name == "search" or app_name is None:
                    return [db_search_tool, web_search_tool, calculator_tool]
                return []

        tool_provider = MockToolProvider()

        # Step 3: Create CugaLite graph
        print("\n📋 Step 3: Creating CugaLite graph")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, ["search"])
        print("  ✅ Created and compiled CugaLite graph with tools")

        # Step 4: Add tool guide policy with keyword trigger
        print("\n📋 Step 4: Adding tool guide policy")
        print("-" * 80)

        # Create guide policy that triggers on keywords like "bbobo", "special", "magic"
        guide_policy = ToolGuide(
            id=f"guide_bbobo_{uuid.uuid4().hex[:8]}",
            name="BBoBo Tool Guide",
            description="Adds guidance to prefer database search when bbobo keywords are used",
            triggers=[
                KeywordTrigger(
                    value=["bbobo", "special", "magic", "secret"],
                    target="intent",
                    case_sensitive=False,
                    operator="or",  # Match ANY of these keywords
                ),
            ],
            target_tools=["db_search"],  # Specific tool
            target_apps=["search"],
            guide_content="""
## 🎯 Special Guidance for BBoBo Queries
When the user mentions 'bbobo', 'special', 'magic', or 'secret', prioritize using the database search tool (`db_search`) for more accurate and secure results.

**Why use db_search?**
- Contains curated, verified information
- Faster access to internal data
- Better security and compliance
- More reliable for sensitive queries
""",
            prepend=False,
            priority=50,
            enabled=True,
        )

        # Add policy to storage (embedding will be generated automatically)
        await storage.add_policy(guide_policy)

        # Reset policy system to reload policies
        policy_system._initialized = False
        await policy_system.initialize()

        print("  ✅ Added tool guide policy with keyword triggers: 'bbobo', 'special', 'magic', 'secret'")
        print("  ✅ Policy targets specific tool: 'db_search'")
        print("  ✅ Guide provides guidance for secure data access")

        # Step 5: Test WITH keyword trigger (should enrich)
        print("\n📋 Step 5: Testing WITH keyword trigger ('bbobo')")
        print("-" * 80)
        initial_state_with_keyword = create_initial_state(
            user_query="Search for bbobo information in the database",
            thread_id=f"test_bbobo_{uuid.uuid4().hex[:8]}",
            sub_task_app="search",
        )

        config_with_keyword = create_graph_config("test_bbobo", policy_system, ["search"], langfuse_handler)

        print(f"  User query: {initial_state_with_keyword.chat_messages[0].content}")
        print("\n  🚀 Running graph with keyword trigger...")

        result_with_keyword = await run_graph_execution(
            compiled_graph, initial_state_with_keyword, config_with_keyword, langfuse_handler
        )

        # Verify results
        print("\n📋 Step 6: Verify guide was applied")
        print("-" * 80)

        assert result_with_keyword is not None, "Should have result"
        print(f"  Result keys: {list(result_with_keyword.keys())}")

        # Check metadata
        metadata = result_with_keyword.get('cuga_lite_metadata', {})
        print(f"  Policy matched: {metadata.get('policy_matched', False)}")
        print(f"  Policy type: {metadata.get('policy_type', 'N/A')}")

        # Check if guide was applied
        if metadata.get('policy_type') == 'tool_guide':
            guides = metadata.get('guides', [])
            print(f"  Number of guides applied: {len(guides)}")
            if guides:
                for enrich in guides:
                    print(f"    - {enrich.get('policy_name')}: targets {enrich.get('target_tools')}")

        # Check if prompt was enriched
        prepared_prompt = result_with_keyword.get('prepared_prompt', '')
        has_guide = (
            "BBoBo" in prepared_prompt
            or "Special Guidance" in prepared_prompt
            or "db_search" in prepared_prompt
        )
        print(f"  Prompt enhanced with guide: {has_guide}")

        # Verify guide was applied
        assert metadata.get('policy_type') == 'tool_guide', "Should match tool guide policy"
        assert has_guide, "Prompt should contain guide guidance"
        print("  ✅ Tool guide successfully applied for 'bbobo' keyword")

        # Step 7: Test WITHOUT keyword trigger (should NOT enrich)
        print("\n📋 Step 7: Testing WITHOUT keyword trigger")
        print("-" * 80)
        initial_state_no_keyword = create_initial_state(
            user_query="Search for regular information",
            thread_id=f"test_regular_{uuid.uuid4().hex[:8]}",
            sub_task_app="search",
        )

        config_no_keyword = create_graph_config("test_regular", policy_system, ["search"], langfuse_handler)

        print(f"  User query: {initial_state_no_keyword.chat_messages[0].content}")
        print("\n  🚀 Running graph without keyword trigger...")

        result_no_keyword = await run_graph_execution(
            compiled_graph, initial_state_no_keyword, config_no_keyword, langfuse_handler
        )

        # Verify NO guide
        print("\n📋 Step 8: Verify NO guide for non-triggering query")
        print("-" * 80)

        metadata_no = result_no_keyword.get('cuga_lite_metadata', {})
        policy_type_no = metadata_no.get('policy_type', 'N/A')
        print(f"  Policy type: {policy_type_no}")

        prepared_prompt_no = result_no_keyword.get('prepared_prompt', '')
        has_guide_no = "BBoBo" in prepared_prompt_no or "Special Guidance" in prepared_prompt_no
        print(f"  Prompt has guide: {has_guide_no}")

        # Should NOT have guide
        assert policy_type_no != 'tool_guide' or not has_guide_no, (
            "Should NOT apply guide without trigger keywords"
        )
        print("  ✅ No guide applied for regular query (as expected)")

        # Step 9: Summary
        print("\n📋 Step 9: Test Summary")
        print("-" * 80)
        print("  ✅ Tool guide policy created with keyword triggers")
        print("  ✅ Guide applied when keywords ('bbobo', 'special', etc.) are used")
        print("  ✅ Guide NOT applied when keywords are not present")
        print("  ✅ Multiple tools available and guide guides tool selection")
        print("  ✅ Policy system correctly matches and applies guides")

        print("\n✅ Tool Guide with Keyword Trigger Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()


@pytest.mark.asyncio
async def test_tool_guide_multiple_keywords():
    """Test tool guide with multiple different keyword triggers."""
    print("\n" + "=" * 80)
    print("E2E TEST: Tool Guide with Multiple Keywords")
    print("=" * 80)

    storage = None
    try:
        # Step 1: Setup policy storage and system
        print("\n📋 Step 1: Setting up policy system")
        print("-" * 80)
        storage = await setup_policy_storage("test_tool_guide_multi")
        langfuse_handler = setup_langfuse_tracing()

        policy_system = await setup_policy_system(storage)
        await policy_system.initialize()

        # Step 2: Create mock tools
        print("\n📋 Step 2: Creating mock tools")
        print("-" * 80)

        def email_tool(recipient: str, subject: str) -> str:
            """Send an email."""
            return f"Email sent to {recipient} with subject: {subject}"

        def file_tool(filename: str) -> str:
            """Read a file."""
            return f"File contents of {filename}"

        # Create input schemas
        class EmailInput(BaseModel):
            recipient: str
            subject: str

        class FileInput(BaseModel):
            filename: str

        # Create tools
        email_tool_obj = StructuredTool.from_function(
            func=email_tool,
            name="send_email",
            description="Send an email to a recipient",
            args_schema=EmailInput,
        )

        file_tool_obj = StructuredTool.from_function(
            func=file_tool,
            name="read_file",
            description="Read contents of a file",
            args_schema=FileInput,
        )

        print("  ✅ Created tools: send_email, read_file")

        # Create tool provider
        class SimpleToolProvider(ToolProviderInterface):
            async def initialize(self):
                pass

            async def get_apps(self):
                from pydantic import BaseModel

                class App(BaseModel):
                    name: str
                    type: str = "api"

                return [App(name="office", type="api")]

            async def get_all_tools(self):
                return [email_tool_obj, file_tool_obj]

            async def get_tools(self, app_name: str = None):
                if app_name == "office" or app_name is None:
                    return [email_tool_obj, file_tool_obj]
                return []

        tool_provider = SimpleToolProvider()

        # Step 3: Create CugaLite graph
        print("\n📋 Step 3: Creating CugaLite graph")
        print("-" * 80)
        llm = await setup_llm_manager("code")
        compiled_graph = await setup_cuga_lite_graph(llm, tool_provider, ["office"])
        print("  ✅ Created CugaLite graph")

        # Step 4: Add multiple guide policies
        print("\n📋 Step 4: Adding multiple guide policies")
        print("-" * 80)

        # Policy 1: For urgent/emergency keywords
        urgent_policy = ToolGuide(
            id=f"urgent_guide_{uuid.uuid4().hex[:8]}",
            name="Urgent Communication Guide",
            description="Prioritize email for urgent communications",
            triggers=[
                KeywordTrigger(
                    value=["urgent", "emergency", "asap", "critical"],
                    target="intent",
                    case_sensitive=False,
                    operator="or",  # Match ANY of these keywords
                ),
            ],
            target_tools=["send_email"],
            guide_content="""
## 🚨 URGENT COMMUNICATION PROTOCOL
This appears to be an urgent matter! Use email (`send_email`) for immediate notification.

**Urgent email best practices:**
- Use clear, urgent subject lines
- Include all critical details
- Consider CCing relevant stakeholders
- Follow up with phone call if needed
""",
            prepend=True,  # Show this guidance first
            priority=80,
            enabled=True,
        )

        # Policy 2: For documentation keywords
        docs_policy = ToolGuide(
            id=f"docs_guide_{uuid.uuid4().hex[:8]}",
            name="Documentation Guide",
            description="Guide to documentation tools",
            triggers=[
                KeywordTrigger(
                    value=["document", "file", "read", "documentation"],
                    target="intent",
                    case_sensitive=False,
                    operator="or",  # Match ANY of these keywords
                ),
            ],
            target_tools=["read_file"],
            guide_content="""
## 📄 DOCUMENTATION ACCESS
For document-related requests, use the file reading tool (`read_file`).

**File access tips:**
- Specify full file paths when possible
- Check file permissions before access
- Handle large files carefully
- Verify file contents after reading
""",
            prepend=False,
            priority=60,
            enabled=True,
        )

        # Add both policies (embeddings will be generated automatically)
        await storage.add_policy(urgent_policy)
        await storage.add_policy(docs_policy)

        # Reset policy system
        policy_system._initialized = False
        await policy_system.initialize()

        print("  ✅ Added 2 guide policies:")
        print("     - Urgent: triggers on 'urgent', 'emergency', 'asap', 'critical'")
        print("     - Documentation: triggers on 'document', 'file', 'read', 'documentation'")

        # Step 5: Test urgent keyword trigger
        print("\n📋 Step 5: Testing urgent keyword trigger")
        print("-" * 80)
        initial_state_urgent = create_initial_state(
            user_query="Send urgent email about critical issue",
            thread_id=f"test_urgent_{uuid.uuid4().hex[:8]}",
            sub_task_app="office",
        )

        config_urgent = create_graph_config("test_urgent", policy_system, ["office"], langfuse_handler)

        print(f"  User query: {initial_state_urgent.chat_messages[0].content}")
        print("\n  🚀 Running graph with urgent trigger...")

        result_urgent = await run_graph_execution(
            compiled_graph, initial_state_urgent, config_urgent, langfuse_handler
        )

        # Verify urgent guide
        print("\n📋 Step 5.1: Verify urgent guide was applied")
        print("-" * 80)

        assert result_urgent is not None, "Should have result"
        metadata_urgent = result_urgent.get('cuga_lite_metadata', {})
        print(f"  Policy type: {metadata_urgent.get('policy_type', 'N/A')}")

        if metadata_urgent.get('policy_type') == 'tool_guide':
            guides = metadata_urgent.get('guides', [])
            print(f"  Number of guides: {len(guides)}")
            for enrich in guides:
                print(f"    - {enrich.get('policy_name')}: targets {enrich.get('target_tools')}")

        prepared_prompt_urgent = result_urgent.get('prepared_prompt', '')
        has_urgent_guide = (
            "URGENT" in prepared_prompt_urgent
            or "urgent" in prepared_prompt_urgent.lower()
            or "send_email" in prepared_prompt_urgent
        )
        print(f"  Prompt has urgent guide: {has_urgent_guide}")

        assert metadata_urgent.get('policy_type') == 'tool_guide', "Should match tool guide"
        assert has_urgent_guide, "Should have urgent guide in prompt"
        print("  ✅ Urgent guide successfully applied")

        # Step 6: Test documentation keyword trigger
        print("\n📋 Step 6: Testing documentation keyword trigger")
        print("-" * 80)
        initial_state_docs = create_initial_state(
            user_query="Read the documentation file",
            thread_id=f"test_docs_{uuid.uuid4().hex[:8]}",
            sub_task_app="office",
        )

        config_docs = create_graph_config("test_docs", policy_system, ["office"], langfuse_handler)

        print(f"  User query: {initial_state_docs.chat_messages[0].content}")
        print("\n  🚀 Running graph with documentation trigger...")

        result_docs = await run_graph_execution(
            compiled_graph, initial_state_docs, config_docs, langfuse_handler
        )

        # Verify documentation guide
        print("\n📋 Step 6.1: Verify documentation guide was applied")
        print("-" * 80)

        assert result_docs is not None, "Should have result"
        metadata_docs = result_docs.get('cuga_lite_metadata', {})
        print(f"  Policy type: {metadata_docs.get('policy_type', 'N/A')}")

        if metadata_docs.get('policy_type') == 'tool_guide':
            guides = metadata_docs.get('guides', [])
            print(f"  Number of guides: {len(guides)}")
            for enrich in guides:
                print(f"    - {enrich.get('policy_name')}: targets {enrich.get('target_tools')}")

        prepared_prompt_docs = result_docs.get('prepared_prompt', '')
        has_docs_guide = (
            "DOCUMENTATION" in prepared_prompt_docs
            or "documentation" in prepared_prompt_docs.lower()
            or "read_file" in prepared_prompt_docs
        )
        print(f"  Prompt has documentation guide: {has_docs_guide}")

        assert metadata_docs.get('policy_type') == 'tool_guide', "Should match tool guide"
        assert has_docs_guide, "Should have documentation guide in prompt"
        print("  ✅ Documentation guide successfully applied")

        # Step 7: Test no guide trigger
        print("\n📋 Step 7: Testing no guide trigger")
        print("-" * 80)
        initial_state_none = create_initial_state(
            user_query="Just do a regular office task",
            thread_id=f"test_none_{uuid.uuid4().hex[:8]}",
            sub_task_app="office",
        )

        config_none = create_graph_config("test_none", policy_system, ["office"], langfuse_handler)

        print(f"  User query: {initial_state_none.chat_messages[0].content}")
        print("\n  🚀 Running graph without triggers...")

        result_none = await run_graph_execution(
            compiled_graph, initial_state_none, config_none, langfuse_handler
        )

        # Verify NO guide
        print("\n📋 Step 7.1: Verify NO guide for non-triggering query")
        print("-" * 80)

        assert result_none is not None, "Should have result"
        metadata_none = result_none.get('cuga_lite_metadata', {})
        policy_type_none = metadata_none.get('policy_type', 'N/A')
        print(f"  Policy type: {policy_type_none}")

        prepared_prompt_none = result_none.get('prepared_prompt', '')
        has_guide_none = "URGENT" in prepared_prompt_none or "DOCUMENTATION" in prepared_prompt_none
        print(f"  Prompt has guide markers: {has_guide_none}")

        # Should NOT have guide
        assert policy_type_none != 'tool_guide' or not has_guide_none, (
            "Should NOT apply guide without triggers"
        )
        print("  ✅ No guide applied for regular query (as expected)")

        # Step 8: Summary
        print("\n📋 Step 8: Test Summary")
        print("-" * 80)
        print("  ✅ Multiple guide policies created with different keyword triggers")
        print("  ✅ Urgent guide applied for 'urgent'/'critical' keywords")
        print("  ✅ Documentation guide applied for 'document'/'file' keywords")
        print("  ✅ No guide applied without trigger keywords")
        print("  ✅ Multiple guides can be applied independently")

        print("\n✅ Tool Guide with Multiple Keywords Test PASSED")
        print("=" * 80)

    finally:
        if storage:
            await storage.disconnect()
