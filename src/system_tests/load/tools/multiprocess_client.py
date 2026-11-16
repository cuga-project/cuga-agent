"""Multi-process load client for one running CUGA stack (manual tool, not a test).

P processes x U users; each user runs R flows on new threads (the two queries of
load_test_with_mocked_llm.py, or only the first with --turns 1). Closed loop, no
state polling, so one Python client process is not the limit. Prints one JSON line.

    uv run python src/system_tests/load/tools/multiprocess_client.py --procs 6 --users-per-proc 10 --rounds 4
"""

import argparse
import asyncio
import json
import multiprocessing as mp
import statistics
import time

import httpx

QUERIES = ("list all my accounts, how many are there?", "how many accounts did we retrieve?")


async def turn(client: httpx.AsyncClient, url: str, thread_id: str, query: str) -> tuple[bool, float, str]:
    started = time.monotonic()
    headers = {"Accept": "text/event-stream", "X-Thread-ID": thread_id}
    answer_next = False
    async with client.stream("POST", f"{url}/stream", json={"query": query}, headers=headers) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event: Answer"):
                answer_next = True
            elif answer_next and line.startswith("data: "):
                ok = "50" in line
                return ok, time.monotonic() - started, "" if ok else f"bad answer: {line[:120]}"
            elif line.startswith("event: Error") or line.startswith("event: error"):
                answer_next = False
                return False, time.monotonic() - started, f"stream {line[:60]}"
    return False, time.monotonic() - started, "stream ended without Answer"


async def user(url: str, rounds: int, turns: int, results: list) -> None:
    async with httpx.AsyncClient(timeout=120.0) as client:
        for _ in range(rounds):
            # /stream continues only server-issued conversations (#774): create one per flow.
            try:
                created = await client.post(f"{url}/api/conversations", json={})
                created.raise_for_status()
                thread_id = created.json()["id"]
            except Exception as exc:  # noqa: BLE001 - classify every failure
                results.append(("error", False, f"create conversation: {type(exc).__name__}"))
                results.append(("flow", False, 0.0))
                continue
            flow_ok = True
            for index, query in enumerate(QUERIES[:turns]):
                try:
                    ok, duration, reason = await turn(client, url, thread_id, query)
                except httpx.HTTPStatusError as exc:
                    ok, duration, reason = False, 0.0, f"HTTP {exc.response.status_code}"
                except Exception as exc:  # noqa: BLE001 - classify every failure
                    ok, duration, reason = False, 0.0, type(exc).__name__
                results.append((index, ok, duration))
                if reason:
                    results.append(("error", False, reason))
                flow_ok = flow_ok and ok
            results.append(("flow", flow_ok, 0.0))


def proc_main(url: str, users: int, rounds: int, turns: int, barrier, queue) -> None:
    results: list = []
    barrier.wait()

    async def run():
        await asyncio.gather(*[user(url, rounds, turns, results) for _ in range(users)])

    asyncio.run(run())
    queue.put(results)


def pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))] if ordered else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    parser.add_argument("--procs", type=int, default=4)
    parser.add_argument("--users-per-proc", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--turns", type=int, default=2, choices=(1, 2))
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(args.procs + 1)
    queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=proc_main, args=(args.url, args.users_per_proc, args.rounds, args.turns, barrier, queue)
        )
        for _ in range(args.procs)
    ]
    for proc in procs:
        proc.start()
    barrier.wait()
    started = time.monotonic()
    results = [item for _ in procs for item in queue.get()]
    wall = time.monotonic() - started
    for proc in procs:
        proc.join()

    flows = [ok for kind, ok, _ in results if kind == "flow"]
    errors: dict[str, int] = {}
    for kind, _, reason in results:
        if kind == "error":
            errors[reason] = errors.get(reason, 0) + 1
    primary = [d for kind, ok, d in results if kind == 0 and ok]
    followup = [d for kind, ok, d in results if kind == 1 and ok]
    print(
        json.dumps(
            {
                "label": args.label,
                "concurrent_users": args.procs * args.users_per_proc,
                "procs": args.procs,
                "turns_per_flow": args.turns,
                "flows": len(flows),
                "flows_ok": sum(flows),
                "wall_s": round(wall, 2),
                "flows_per_s": round(sum(flows) / wall, 2),
                "primary_p50_s": round(pct(primary, 50), 2),
                "primary_p95_s": round(pct(primary, 95), 2),
                "followup_p50_s": round(pct(followup, 50), 2),
                "mean_primary_s": round(statistics.mean(primary), 2) if primary else 0.0,
                "errors": errors,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
