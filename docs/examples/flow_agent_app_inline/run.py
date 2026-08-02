#!/usr/bin/env python3
"""
Run any flow_agent_app_inline process from the command line.

Usage:
    python run.py loan_approval
    python run.py receive_order
    python run.py trip_planner
"""

import asyncio
import sys
from pathlib import Path

from cuga.backend.cuga_graph.nodes.cuga_flow.flow_config import FlowConfig

# Default input data per process
DEFAULT_INPUTS = {
    "loan_approval": {
        "applicant_name": "John Doe",
        "applicant_id": "A12345",
        "loan_amount": 1000,
        "credit_history": "Excellent payment history, no defaults",
        "income": 75000,
        "employment_years": 5,
    },
    # Same inputs as loan_approval — the Kogito app is the same process on a different
    # engine, so identical inputs make the two directly comparable.
    "loan_approval_kogito": {
        "applicant_name": "John Doe",
        "applicant_id": "A12345",
        "loan_amount": 1000,
        "credit_history": "Excellent payment history, no defaults",
        "income": 75000,
        "employment_years": 5,
    },
    "receive_order": {
        "order_id": "ORD-001",
        "customer_name": "Acme Corp",
        "item": "Widget X",
        "quantity": 10,
        "unit_price": 25.0,
    },
    "trip_planner": {
        "destination": "Paris",
        "start_date": "2026-07-01",
        "end_date": "2026-07-07",
        "budget": 3000,
        "travelers": 2,
    },
}


async def main(process_name: str) -> None:
    base_dir = Path(__file__).parent / process_name

    # Find the config YAML (named <process_name>_config.yaml)
    config_candidates = list((base_dir / "config").glob("*_config.yaml"))
    if not config_candidates:
        print(f"No *_config.yaml found under {base_dir / 'config'}")
        sys.exit(1)
    config_file = config_candidates[0]

    print(f"Process  : {process_name}")
    print(f"Config   : {config_file}")

    flow_config = FlowConfig.from_yaml(str(config_file))
    flow_agent = flow_config.to_flow_agent()

    input_data = DEFAULT_INPUTS.get(process_name, {})
    print(f"Input    : {input_data}")
    print()

    result = await flow_agent.invoke(input_data)

    print("Result:")
    if hasattr(result, "model_dump"):
        import json
        print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    else:
        print(result)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {Path(__file__).name} <process_name>")
        print(f"Available: {', '.join(DEFAULT_INPUTS)}")
        sys.exit(1)

    asyncio.run(main(sys.argv[1]))
