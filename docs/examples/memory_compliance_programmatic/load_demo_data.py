"""Load the same conversations and memories used by the compliance UI PoC."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8201/sse")
    parser.add_argument("--namespace", default="evolve")
    parser.add_argument("--agent-id", default="cuga-default")
    parser.add_argument("--user-id", default="default_user")
    parser.add_argument("--user-name", default="Demo user")
    parser.add_argument(
        "--cuga-db",
        type=Path,
        default=Path("/tmp/cuga-memory-programmer-poc.db"),
        help="Local CUGA SQLite database that will receive conversations and the PoC ledger.",
    )
    parser.add_argument(
        "--skip-simulation",
        action="store_true",
        help="Load conversations and memories without creating a simulated scheduled run.",
    )
    parser.add_argument(
        "--simulate-again",
        action="store_true",
        help="Create another simulated scheduled run even when the seed already exists.",
    )
    args = parser.parse_args()

    args.cuga_db.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DYNACONF_EVOLVE__ENABLED", "true")
    os.environ.setdefault("DYNACONF_EVOLVE__LITE_MODE_ONLY", "false")
    os.environ.setdefault("DYNACONF_EVOLVE__MODE", "direct")
    os.environ["DYNACONF_EVOLVE__URL"] = args.url
    os.environ["DYNACONF_STORAGE__LOCAL_DB_PATH"] = str(args.cuga_db.resolve())

    from cuga.backend.evolve.compliance_poc import bootstrap, run_simulated_schedule

    loaded = await bootstrap(
        args.agent_id,
        args.user_id,
        args.namespace,
        args.user_name,
    )

    simulation = None
    should_simulate = not args.skip_simulation and (
        not loaded.get("already_completed") or args.simulate_again
    )
    if should_simulate:
        simulation = await run_simulated_schedule(
            args.agent_id,
            args.namespace,
            args.user_id,
            dry_run=True,
        )

    result = {
        "seed_id": loaded["seed_id"],
        "already_completed": bool(loaded.get("already_completed")),
        "cuga_db": str(args.cuga_db.resolve()),
        "evolve_url": args.url,
        "namespace_id": loaded["namespace_id"],
        "agent_id": loaded["agent_id"],
        "conversation_count": len(loaded["conversation_ids"]),
        "memory_count": loaded["memory_count"],
        "created_memories": loaded["created_entities"],
        "protection_healthy": bool((loaded.get("protection_status") or {}).get("healthy")),
        "simulated_schedule_created": simulation is not None,
        "simulated_schedule_summary": (simulation or {}).get("summary"),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
