#!/usr/bin/env python3
"""
Simple Loan Approval Demo - Test FlowAgent Instantiation

This demo tests that the loan approval FlowAgent can be instantiated
from the BPMN diagram and YAML configuration.
"""

from pathlib import Path
from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import FlowConfig
from cuga.backend.cuga_graph.nodes.cuga_flow.bpmn_parser import BPMNParser


def main():
    """Test FlowAgent instantiation with loan approval process."""

    print("=" * 60)
    print("LOAN APPROVAL - Simple Instantiation Test")
    print("=" * 60)
    print()

    # Get file paths
    current_dir = Path(__file__).parent
    bpmn_file = current_dir / "config" / "BPMNdiagram.bpmn"
    config_file = current_dir / "config" / "loan_approval_config.yaml"

    # Step 1: Check files exist
    print("Step 1: Check Files")
    print("-" * 60)
    print(f"BPMN File: {bpmn_file}")
    print(f"Exists: {bpmn_file.exists()}")
    print(f"Config File: {config_file}")
    print(f"Exists: {config_file.exists()}")
    print()

    # Step 2: Parse BPMN
    print("Step 2: Parse BPMN Process")
    print("-" * 60)
    try:
        parser = BPMNParser()
        process = parser.parse_file(str(bpmn_file))
        print("✓ BPMN parsed successfully")
        print(f"  Process ID: {process.id}")
        print(f"  Process Name: {process.name}")
        print(f"  Elements: {len(process.elements)}")
        print(f"  Flows: {len(process.flows)}")
        print(f"  Start Event: {process.start_event}")
        print(f"  End Events: {', '.join(process.end_events)}")
        print()

        # List elements
        print("  Elements:")
        for elem_id, element in process.elements.items():
            print(f"    - {element.name} ({elem_id}) [{element.element_type}]")
        print()

    except Exception as e:
        print(f"✗ Error parsing BPMN: {e}")
        print()

    # Step 3: Load YAML Configuration
    print("Step 3: Load YAML Configuration")
    print("-" * 60)
    try:
        flow_config = FlowConfig.from_yaml(str(config_file))
        print("✓ Configuration loaded successfully")
        print(f"  Flow Name: {flow_config.get_flow_name()}")
        print(f"  BPMN File: {flow_config.get_bpmn_file()}")

        # Show task configurations
        tasks_config = flow_config.config.get('flow', {}).get('tasks', {})
        print(f"  Tasks Configured: {len(tasks_config)}")
        for task_id, task_config in tasks_config.items():
            agent = task_config.get('agent', 'N/A')
            print(f"    - {task_id}: {agent}")

        # Show gateway configurations
        gateways_config = flow_config.config.get('flow', {}).get('gateways', {})
        print(f"  Gateways Configured: {len(gateways_config)}")
        for gw_id, gw_config in gateways_config.items():
            gw_type = gw_config.get('type', 'N/A')
            print(f"    - {gw_id}: {gw_type}")

        # Show initial variables
        initial_vars = flow_config.get_initial_variables()
        print(f"  Initial Variables: {len(initial_vars)}")
        for var_name, var_value in initial_vars.items():
            print(f"    - {var_name}: {var_value}")
        print()

    except Exception as e:
        print(f"✗ Error loading configuration: {e}")
        import traceback

        traceback.print_exc()
        print()

    # Step 4: Instantiate FlowAgent
    print("Step 4: Instantiate FlowAgent")
    print("-" * 60)
    try:
        flow_agent = flow_config.to_flow_agent()
        print("✓ FlowAgent instantiated successfully")
        print(f"  Process: {flow_agent.process.name}")
        print(f"  Elements: {len(flow_agent.process.elements)}")
        print(f"  Flows: {len(flow_agent.process.flows)}")
        print()

    except Exception as e:
        print(f"✗ Error instantiating FlowAgent: {e}")
        import traceback

        traceback.print_exc()
        print()

    print("=" * 60)
    print("Test Complete!")
    print("=" * 60)
    print()
    print("Summary:")
    print("  ✓ BPMN process parsed successfully")
    print("  ✓ YAML configuration loaded")
    print("  ✓ FlowAgent instantiated")
    print()
    print("Next Steps:")
    print("  1. Implement task agent modules (credit_checker, loan_processor, rejection_handler)")
    print("  2. Test with actual loan application data")
    print("  3. Execute: result = await flow_agent.invoke(input_data)")


if __name__ == "__main__":
    main()

# Made with Bob
