"""Exercise Evolve memory through CUGA's existing integration layer."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import yaml


def _print_json(label: str, value: Any) -> None:
    print(f"\n{label}")
    print(json.dumps(value, indent=2, default=str))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8201/sse")
    parser.add_argument("--namespace", default="evolve")
    parser.add_argument("--agent-id", default="cuga-default")
    parser.add_argument("--user-id")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--forget", metavar="ENTITY_ID")
    parser.add_argument("--legal-hold", metavar="ENTITY_ID")
    args = parser.parse_args()

    # Dynaconf reads these settings when the CUGA integration is imported.
    os.environ.setdefault("DYNACONF_EVOLVE__ENABLED", "true")
    os.environ.setdefault("DYNACONF_EVOLVE__LITE_MODE_ONLY", "false")
    os.environ.setdefault("DYNACONF_EVOLVE__MODE", "direct")
    os.environ["DYNACONF_EVOLVE__URL"] = args.url

    from cuga.backend.evolve.integration import EvolveIntegration

    status = await EvolveIntegration.get_compliance_status(namespace_id=args.namespace)
    _print_json("Protection and retention status", status)

    inventory = await EvolveIntegration.list_entities(
        user_id=args.user_id,
        agent_id=args.agent_id,
        namespace_id=args.namespace,
        limit=args.limit,
        include_content=True,
        record_access=False,
    )
    _print_json("Memory inventory", inventory)

    if args.legal_hold:
        held = await EvolveIntegration.patch_entity_metadata(
            args.legal_hold,
            {"legal_hold": True},
            user_id=args.user_id,
            agent_id=args.agent_id,
            namespace_id=args.namespace,
        )
        _print_json("Legal hold applied", held)

    if args.forget:
        forgotten = await EvolveIntegration.delete_entity(
            args.forget,
            user_id=args.user_id,
            agent_id=args.agent_id,
            namespace_id=args.namespace,
        )
        _print_json("Forget result", forgotten)

    policy_path = Path(__file__).with_name("retention.yaml")
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    validation = await EvolveIntegration.validate_retention_policy(policy)
    _print_json("Retention policy validation", validation)

    report = await EvolveIntegration.run_retention(
        policy,
        dry_run=True,
        namespace_id=args.namespace,
        metadata_filters={"agent_id": args.agent_id},
    )
    _print_json("Retention dry run", report)


if __name__ == "__main__":
    asyncio.run(main())
