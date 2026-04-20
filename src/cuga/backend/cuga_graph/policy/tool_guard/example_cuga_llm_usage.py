"""
Example usage of CugaLLMAdapter with ToolGuard.

This demonstrates how to use CUGA's LLM system with ToolGuard's
example generation capabilities.
"""

import asyncio
from cuga.backend.cuga_graph.nodes.cuga_lite.combined_tool_provider import CombinedToolProvider
from cuga.sdk import CugaAgent
from cuga.backend.cuga_graph.policy.tool_guard.cuga_llm_adapter import CugaLLMAdapter
from langchain_core.tools import tool


@tool
def delete_file(path: str) -> str:
    """Delete a file from the filesystem"""
    return f"Deleted file: {path}"


@tool
def read_file(path: str) -> str:
    """Read contents of a file"""
    return f"Contents of {path}"


async def example_basic_usage():
    """Basic example: Using CugaLLMAdapter directly"""
    print("\n=== Example 1: Basic CugaLLMAdapter Usage ===\n")
    
    # Create a CUGA agent with tools
    agent = CugaAgent(tools=[delete_file, read_file])
    
    # Wrap the agent's model with the adapter
    assert agent._model is not None, "Agent model should be initialized"
    llm_adapter = CugaLLMAdapter(agent._model)
    
    # Use the adapter with ToolGuard's message format
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Say hello in a friendly way."}
    ]
    
    response = await llm_adapter.generate(messages)
    print(f"Response: {response}\n")


async def example_json_output():
    """Example: Using chat_json for structured output"""
    print("\n=== Example 2: JSON Output with chat_json ===\n")
    
    agent = CugaAgent(tools=[delete_file])
    assert agent._model is not None, "Agent model should be initialized"
    llm_adapter = CugaLLMAdapter(agent._model)
    
    # Request JSON output
    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant that responds in JSON format."
        },
        {
            "role": "user",
            "content": (
                "Generate a JSON object with two fields: 'greeting' and 'language'. "
                "Wrap your response in ```json``` code blocks."
            )
        }
    ]
    
    # chat_json automatically extracts JSON from the response
    json_response = await llm_adapter.chat_json(messages)
    print(f"JSON Response: {json_response}\n")


async def example_with_toolguard_manager():
    """Example: Using ToolGuardManager with CugaLLMAdapter"""
    print("\n=== Example 3: ToolGuardManager Integration ===\n")
    
    from cuga.backend.cuga_graph.policy.tool_guard.manager import ToolGuardManager
    
    # Create agent with tools
    agent = CugaAgent(
        tools=[delete_file, read_file],
        cuga_folder="/Users/naamazwerdling/Documents/OASB/policy_validation/cuga_demo/output"
    )
    
    # Create ToolGuard manager (automatically uses CugaLLMAdapter)
    manager = ToolGuardManager(agent)
    await manager.initialize()
    
    tool_count = len(manager.langchain_tools) if manager.langchain_tools else 0
    print(f"✅ ToolGuardManager initialized with {tool_count} tools")
    print(f"   LLM: {type(manager.llm).__name__}")
    print(f"   Underlying model: {type(agent._model).__name__}\n")
    
    # Add a tool guide policy
    await agent.policies.add_tool_guide(
        name="File Safety Guidelines",
        target_tools=["delete_file"],
        description="Ensure safe file deletion practices",
        content="""
# File Safety Guidelines

## Rules
- Never delete system files (e.g., /etc/*, /sys/*, /boot/*)
- Never delete files without user confirmation
- Always validate file paths before deletion

## Examples
- ❌ BAD: Delete /etc/passwd
- ✅ GOOD: Delete /home/user/temp.txt (with confirmation)
        """.strip()
    )
    
    print("✅ Added tool guide policy for delete_file\n")
    
    # Get the policy
    policies = await agent.policies.list()
    if policies:
        policy_info = policies[0]
        print(f"Policy: {policy_info['name']} (ID: {policy_info['id']})\n")
        
        # Generate examples using ToolGuard with CUGA's LLM
        print("🔄 Generating violating and compliance examples...")
        policy_full = await agent.policies.get(policy_info['id'])
        
        if policy_full and policy_full.get('policy'):
            try:
                violating, compliance = await manager.generate_examples(
                    policy=policy_full['policy'],
                    target_tool="delete_file"
                )
                
                print("\n📋 Generated Examples:\n")
                
                print("❌ Violating Examples (actions that break the policy):")
                for i, example in enumerate(violating, 1):
                    print(f"   {i}. {example}")
                
                print("\n✅ Compliance Examples (actions that follow the policy):")
                for i, example in enumerate(compliance, 1):
                    print(f"   {i}. {example}")
                
                print()
                
            except Exception as e:
                print(f"⚠️  Error generating examples: {e}")
                import traceback
                print(traceback.format_exc())
                print("   This might happen if the policy format is incompatible or LLM fails")
                print()
        else:
            print("⚠️  Could not retrieve full policy details\n")


async def example_error_handling():
    """Example: Error handling with CugaLLMAdapter"""
    print("\n=== Example 4: Error Handling ===\n")
    
    agent = CugaAgent(tools=[delete_file])
    assert agent._model is not None, "Agent model should be initialized"
    llm_adapter = CugaLLMAdapter(agent._model)
    
    # Test with invalid message format
    try:
        invalid_messages = [
            {"content": "Missing role key"}  # Missing 'role'
        ]
        await llm_adapter.generate(invalid_messages)
    except ValueError as e:
        print(f"✅ Caught expected error: {e}\n")
    
    # Test with valid messages
    try:
        valid_messages = [
            {"role": "user", "content": "Hello!"}
        ]
        response = await llm_adapter.generate(valid_messages)
        print(f"✅ Valid request succeeded: {response[:50]}...\n")
    except Exception as e:
        print(f"❌ Unexpected error: {e}\n")


async def main():
    """Run all examples"""
    print("=" * 60)
    print("CugaLLMAdapter Usage Examples")
    print("=" * 60)
    
    try:
        #await example_basic_usage()
        #await example_json_output()
        await example_with_toolguard_manager()
        #await example_error_handling()
        
        print("=" * 60)
        print("✅ All examples completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Error running examples: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

# Made with Bob
