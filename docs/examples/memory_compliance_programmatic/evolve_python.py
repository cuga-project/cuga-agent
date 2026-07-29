"""Inspect and administer the same local memory using Evolve's Python client."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.retention import RetentionEngine, RetentionPolicy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--namespace", default="evolve")
    parser.add_argument("--agent-id", default="cuga-default")
    parser.add_argument("--user-id")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--forget", metavar="ENTITY_ID")
    parser.add_argument("--legal-hold", metavar="ENTITY_ID")
    args = parser.parse_args()

    client = EvolveClient()
    filters = {"metadata.agent_id": args.agent_id}
    if args.user_id:
        filters["metadata.user_id"] = args.user_id

    entities = client.scan_entities(args.namespace, filters=filters, limit=args.limit)
    print(f"Backend healthy: {client.ready()}")
    print(f"Memories found: {len(entities)}")
    for entity in entities:
        metadata = entity.metadata or {}
        title = metadata.get("title") or str(entity.content)[:72]
        print(f"- {entity.id} [{entity.type}] {title}")

    if args.legal_hold:
        updated = client.patch_entity_metadata(
            args.namespace,
            args.legal_hold,
            {"legal_hold": True},
        )
        print("\nLegal hold applied")
        print(json.dumps(updated.model_dump(mode="json"), indent=2, default=str))

    if args.forget:
        client.delete_entity_by_id(args.namespace, args.forget)
        print(f"\nForgotten: {args.forget}")

    policy = RetentionPolicy.from_file(
        Path(__file__).with_name("retention.yaml")
    )
    report = RetentionEngine(client).apply(
        args.namespace,
        policy,
        dry_run=True,
        filters=filters,
    )
    print(f"\nRetention dry run: {report.summary()}")
    for item in [*report.flagged, *report.deleted, *report.skipped]:
        print(f"- {item.action}: {item.entity_id} ({item.detail})")


if __name__ == "__main__":
    main()
