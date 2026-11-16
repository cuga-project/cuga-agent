"""Capacity ramp for one running CUGA stack (manual tool, not a test).

Steps through concurrent-user levels. Each level holds all its users for ``--hold-s`` seconds;
every user repeats: new thread -> turn 1 -> think -> turn 2 -> think. Think time and the mock LLM
delay (``CUGA_MOCK_LLM_LATENCY_MS`` on the server) make a conversation last as long as it does
with a real LLM, so the number of users that stay active is what is measured, not peak throughput.

A level passes when the turn p95 is under ``--slo-p95-s`` and the error rate is under
``--max-error-rate``. The reported capacity is the largest passing level.

    CUGA_MOCK_LLM_LATENCY_MS=3000 <start the stack>
    uv run python src/system_tests/load/tools/capacity_ramp.py --levels 50,100,200,300 --think-s 10
"""

import argparse
import asyncio
import json
import multiprocessing as mp
import random
import statistics
import time
import uuid

import httpx

from system_tests.load.tools.multiprocess_client import QUERIES, pct, turn


async def user(url: str, turns: int, think_s: float, start_at: float, end_at: float, results: list) -> None:
    """One user: wait for its start offset, then run conversations until ``end_at`` (wall clock)."""
    await asyncio.sleep(max(0.0, start_at - time.time()))
    async with httpx.AsyncClient(timeout=300.0) as client:
        while time.time() < end_at:
            thread_id = str(uuid.uuid4())
            for index, query in enumerate(QUERIES[:turns]):
                if time.time() >= end_at:
                    return
                started_at = time.time()
                try:
                    ok, duration, reason = await turn(client, url, thread_id, query)
                except httpx.HTTPStatusError as exc:
                    ok, duration, reason = False, time.time() - started_at, f"HTTP {exc.response.status_code}"
                except Exception as exc:  # noqa: BLE001 - classify every failure
                    ok, duration, reason = False, time.time() - started_at, type(exc).__name__
                results.append((started_at, index, ok, duration, reason))
                await asyncio.sleep(think_s * random.uniform(0.5, 1.5))


def proc_main(url, users, turns, think_s, start_at, ramp_up_s, end_at, queue) -> None:
    results: list = []

    async def run():
        offsets = [start_at + random.uniform(0.0, ramp_up_s) for _ in range(users)]
        await asyncio.gather(*[user(url, turns, think_s, o, end_at, results) for o in offsets])

    asyncio.run(run())
    queue.put(results)


def run_level(args, users: int) -> dict:
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    start_at = time.time() + 2.0
    end_at = start_at + args.ramp_up_s + args.hold_s
    per_proc = [users // args.procs + (1 if i < users % args.procs else 0) for i in range(args.procs)]
    procs = [
        ctx.Process(
            target=proc_main,
            args=(args.url, n, args.turns, args.think_s, start_at, args.ramp_up_s, end_at, queue),
        )
        for n in per_proc
        if n
    ]
    for proc in procs:
        proc.start()
    rows = [row for _ in procs for row in queue.get()]
    for proc in procs:
        proc.join()

    # Measure only the steady part: after every user has started.
    window_start = start_at + args.ramp_up_s
    window = max(end_at - window_start, 1e-9)
    steady = [r for r in rows if r[0] >= window_start]
    ok_rows = [r for r in steady if r[2]]
    errors: dict[str, int] = {}
    for _, _, ok, _, reason in steady:
        if not ok:
            errors[reason or "failed"] = errors.get(reason or "failed", 0) + 1
    latencies = [r[3] for r in ok_rows]
    error_rate = (len(steady) - len(ok_rows)) / len(steady) if steady else 1.0
    p95 = pct(latencies, 95)
    return {
        "label": args.label,
        "users": users,
        "think_s": args.think_s,
        "hold_s": args.hold_s,
        "turns": len(steady),
        "turns_per_s": round(len(ok_rows) / window, 2),
        "p50_s": round(pct(latencies, 50), 2),
        "p95_s": round(p95, 2),
        "p99_s": round(pct(latencies, 99), 2),
        "mean_s": round(statistics.mean(latencies), 2) if latencies else 0.0,
        # By Little's law: average requests being served at the same time.
        "avg_in_flight": round(sum(latencies) / window, 1),
        "error_rate": round(error_rate, 4),
        "errors": errors,
        "passes": bool(steady) and p95 <= args.slo_p95_s and error_rate <= args.max_error_rate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--levels", default="50,100,200,300", help="comma list of concurrent users")
    parser.add_argument(
        "--procs", type=int, default=4, help="client processes (keep the client off the limit)"
    )
    parser.add_argument("--hold-s", type=float, default=120.0, help="steady seconds per level")
    parser.add_argument("--ramp-up-s", type=float, default=15.0, help="users start spread over this time")
    parser.add_argument("--think-s", type=float, default=10.0, help="mean pause after each turn (0.5x-1.5x)")
    parser.add_argument("--turns", type=int, default=2, choices=(1, 2))
    parser.add_argument("--slo-p95-s", type=float, default=30.0, help="turn p95 target")
    parser.add_argument("--max-error-rate", type=float, default=0.01)
    parser.add_argument(
        "--stop-after-fails", type=int, default=2, help="stop after this many failing levels in a row"
    )
    parser.add_argument("--out", default="", help="JSONL file to append one row per level")
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    capacity = 0
    failed_in_a_row = 0
    for users in [int(x) for x in args.levels.split(",")]:
        row = run_level(args, users)
        print(json.dumps(row), flush=True)
        if args.out:
            with open(args.out, "a") as f:
                f.write(json.dumps(row) + "\n")
        if row["passes"]:
            capacity, failed_in_a_row = users, 0
        else:
            failed_in_a_row += 1
            if failed_in_a_row >= args.stop_after_fails:
                break
    print(
        json.dumps(
            {
                "summary": "capacity",
                "label": args.label,
                "largest_passing_users": capacity,
                "slo": {"p95_s": args.slo_p95_s, "max_error_rate": args.max_error_rate},
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
