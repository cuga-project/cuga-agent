"""
Debug script for creating, storing, retrieving, and exporting a tool guide policy.

This script demonstrates:
1. Creating a CugaAgent with a tool
2. Adding a new tool guide policy
3. Saving the policy
4. Retrieving the policy from storage
5. Exporting the policy data to a JSON file
"""

import asyncio
import json
from pathlib import Path
from datetime import datetime

from langchain_core.tools import tool

from cuga import CugaAgent
from cuga.backend.cuga_graph.policy.models import ToolGuide


@tool
def delete_file(file_path: str) -> str:
    """Delete a file from the system"""
    return f"File {file_path} has been deleted"


async def main():
    """Main workflow for creating and managing a tool guide policy."""
    
    # Step 1: Create a CugaAgent with the delete_file tool
    print("Step 1: Creating CugaAgent with delete_file tool...")
    agent = CugaAgent(tools=[delete_file])
    
    # Step 2: Add a new tool guide policy
    print("\nStep 2: Adding new tool guide policy...")
    policy_id = await agent.policies.add_tool_guide(
        name="File Deletion Safety Policy",
        content="## File Deletion Guidelines\n- Never delete system files\n- Always verify file path before deletion\n- Require confirmation for critical files\n- Log all deletion operations",
        target_tools=["delete_file"],
        description="Safety guidelines for file deletion operations to prevent accidental data loss",
    )
    print(f"Created policy with ID: {policy_id}")
    
    # Step 2b: Update the policy with tool_guards
    print("\nStep 2b: Updating policy with tool_guards...")
    await agent.policies.update_tool_guard(
        policy_id=policy_id,
        tool_guards={
            "delete_file": {
                "description": "Guard rules for safe file deletion to prevent accidental data loss",
                "violating_examples": [
                    "Deleting system files like /etc/passwd or C:\\Windows\\System32",
                    "Deleting files without user confirmation",
                    "Deleting files in protected directories",
                    "Bulk deletion without verification"
                ],
                "compliance_examples": [
                    "Verify file path is in user's home directory before deletion",
                    "Request explicit user confirmation before deleting",
                    "Check file is not a system file or in protected directory",
                    "Log all deletion operations with timestamp and user"
                ],
                "policy_code": ""
            }
        }
    )
    print(f"Updated policy {policy_id} with tool_guards")
    
    # Step 3: Save the policy
    print("\nStep 3: Saving policy...")
    policy_tool_guide = await agent.policies.get(policy_id)
    if policy_tool_guide is None:
        raise ValueError(f"Failed to retrieve policy {policy_id}")
    
    policy = policy_tool_guide["policy"]
    
    # Get policy system and update
    policy_system = await agent.policies._ensure_policy_system()
    if policy_system is None or policy_system.storage is None:
        raise ValueError("Policy system storage is not available")
    
    #await policy_system.storage.update_policy(policy)
    await policy_system.initialize()
    
    # Save to file system if available
    if agent.policies._fs_sync:
        agent.policies._fs_sync.save_policy_to_file(policy)
    
    print("Policy saved successfully!")
    
    # Step 4: Retrieve the policy from storage
    print("\nStep 4: Retrieving policy from storage...")
    retrieved_policy = await policy_system.storage.get_policy(policy_id)
    if retrieved_policy is None:
        raise ValueError(f"Failed to retrieve updated policy {policy_id}")
    
    if not isinstance(retrieved_policy, ToolGuide):
        raise TypeError(f"Expected ToolGuide, got {type(retrieved_policy).__name__}")
    
    print("Policy retrieved successfully!")
    
    # Step 5: Export to JSON file
    print("\nStep 5: Exporting policy data to JSON file...")
    
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
    output_path = Path("tool_guide_policy_export.json")
    output_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
    
    print(f"\n{'='*60}")
    print("SUCCESS! Policy workflow completed:")
    print(f"{'='*60}")
    print(f"Policy ID: {policy_id}")
    print(f"Policy Name: {retrieved_policy.name}")
    print(f"Target Tools: {', '.join(retrieved_policy.target_tools)}")
    print(f"\nExported to: {output_path.absolute()}")
    print(f"{'='*60}")
    print("\nExported JSON content:")
    print(json.dumps(output_data, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

# Made with Bob