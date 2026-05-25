#!/usr/bin/env python3
"""
Loan Approval Process Demo

This demo shows how to use FlowAgent with the loan approval BPMN process.
It demonstrates credit checking, decision making, and loan processing.
"""

import asyncio
from pathlib import Path

from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import FlowConfig


async def main():
    """Run the loan approval demo."""

    print("=" * 60)
    print("LOAN APPROVAL PROCESS DEMO")
    print("=" * 60)
    print()

    # Get file paths
    current_dir = Path(__file__).parent
    config_file = current_dir / "config" / "loan_approval_config.yaml"

    print(f"Config File: {config_file}")
    print()

    # Load configuration and create FlowAgent
    print("Loading configuration...")
    flow_config = FlowConfig.from_yaml(str(config_file))

    print("Creating FlowAgent...")
    flow_agent = flow_config.to_flow_agent()

    print(f"✓ FlowAgent created: {flow_config.get_flow_name()}")
    print(f"  BPMN File: {flow_config.get_bpmn_file()}")
    print(f"  Elements: {len(flow_agent.process.elements)}")
    print(f"  Flows: {len(flow_agent.process.flows)}")
    print()

    # Test Case 1: High credit score (should approve)
    print("-" * 60)
    print("TEST CASE 1: High Credit Score (Approval Expected)")
    print("-" * 60)

    input_data_1 = {
        "applicant_name": "John Doe",
        "applicant_id": "A12345",
        "loan_amount": 1000,
        "credit_history": "Excellent payment history, no defaults",
        "income": 75000,
        "employment_years": 5,
    }

    print("Input:")
    for key, value in input_data_1.items():
        print(f"  {key}: {value}")
    print()

    print("Executing flow...")
    result_1 = await flow_agent.invoke(input_data_1)

    print("\nResult:")
    print(f"  Credit Score: {result_1.get('credit_score', 'N/A')}")
    print(f"  Approved: {result_1.get('approved', False)}")
    print(f"  Loan Granted: {result_1.get('loan_granted', False)}")
    if result_1.get('rejection_reason'):
        print(f"  Rejection Reason: {result_1['rejection_reason']}")
    print(f"  Execution Path: {' -> '.join(result_1.get('execution_path', []))}")
    print()

    # Test Case 2: Low credit score (should reject)
    print("-" * 60)
    print("TEST CASE 2: Low Credit Score (Rejection Expected)")
    print("-" * 60)

    input_data_2 = {
        "applicant_name": "Jane Smith",
        "applicant_id": "B67890",
        "loan_amount": 1000,
        "credit_history": "Multiple defaults, late payments",
        "income": 25000,
        "employment_years": 1,
    }

    print("Input:")
    for key, value in input_data_2.items():
        print(f"  {key}: {value}")
    print()

    print("Executing flow...")
    result_2 = await flow_agent.invoke(input_data_2)

    print("\nResult:")
    print(f"  Credit Score: {result_2.get('credit_score', 'N/A')}")
    print(f"  Approved: {result_2.get('approved', False)}")
    print(f"  Loan Granted: {result_2.get('loan_granted', False)}")
    if result_2.get('rejection_reason'):
        print(f"  Rejection Reason: {result_2['rejection_reason']}")
    print(f"  Execution Path: {' -> '.join(result_2.get('execution_path', []))}")
    print()

    print("=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())

# Made with Bob
