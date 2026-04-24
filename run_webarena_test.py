"""Run CUGA agent on WebArena tasks using its own browser.

Usage:
    source /tmp/cuga_env.sh
    python run_webarena_test.py --task-ids 211 340 --output results.json
    python run_webarena_test.py --random 20 --output results.json
"""

# Patch Pydantic to strip markdown JSON fences (Claude compatibility)
import cuga.patches.strip_json_fences  # noqa: F401

import argparse
import asyncio
import importlib.resources
import json
import os
import random
import re
import time
from datetime import datetime, timezone


def load_expected_answers():
    """Load expected answers from webarena test.raw.json."""
    os.environ.setdefault("SHOPPING", os.environ.get("WA_SHOPPING", ""))
    os.environ.setdefault("SHOPPING_ADMIN", os.environ.get("WA_SHOPPING_ADMIN", ""))
    os.environ.setdefault("REDDIT", os.environ.get("WA_REDDIT", ""))
    os.environ.setdefault("GITLAB", os.environ.get("WA_GITLAB", ""))
    os.environ.setdefault("WIKIPEDIA", os.environ.get("WA_WIKIPEDIA", ""))
    os.environ.setdefault("MAP", os.environ.get("WA_MAP", ""))
    os.environ.setdefault("HOMEPAGE", os.environ.get("WA_HOMEPAGE", ""))
    import webarena
    configs = json.loads(
        importlib.resources.files(webarena).joinpath("test.raw.json").read_text()
    )
    return {c["task_id"]: c for c in configs}


def check_answer(agent_answer: str, task_config: dict) -> dict:
    """Compare agent answer against expected. Returns match details."""
    if not agent_answer or agent_answer == "N/A":
        return {"correct": False, "reason": "no answer"}

    ref = task_config.get("eval", {}).get("reference_answers") or {}
    eval_types = task_config.get("eval", {}).get("eval_types") or []
    clean = agent_answer.replace("**", "").strip()

    if "exact_match" in ref:
        expected = ref["exact_match"]
        if clean.lower() == expected.lower():
            return {"correct": True, "reason": f"exact_match '{expected}'"}
        if expected.lower() in clean.lower():
            return {"correct": True, "reason": f"contains '{expected}' (verbose)"}
        return {"correct": False, "reason": f"expected '{expected}', got '{clean[:50]}'"}

    if "must_include" in ref:
        missing = [v for v in ref["must_include"] if v.lower() not in clean.lower()]
        if not missing:
            return {"correct": True, "reason": "all must_include found"}
        return {"correct": False, "reason": f"missing: {missing}"}

    if "fuzzy_match" in ref:
        return {"correct": None, "reason": f"fuzzy_match (manual check): {ref['fuzzy_match']}"}

    if "program_html" in eval_types:
        return {"correct": None, "reason": "program_html (needs live page check)"}

    return {"correct": None, "reason": "unknown eval type"}


async def run_single_task(task_id: int) -> dict:
    """Run CUGA on a single WebArena task."""
    from cuga.backend.cuga_graph.utils.controller import AgentRunner

    print(f"\n{'='*60}")
    print(f"Task {task_id}: Starting...")
    start = time.time()

    try:
        # Reset the global tracker to prevent state pollution between tasks
        import cuga.backend.cuga_graph.utils.controller as ctrl
        ctrl.tracker.steps = []
        ctrl.tracker.actions_count = 0
        ctrl.tracker.final_answer = ""
        if hasattr(ctrl.tracker, 'images'):
            ctrl.tracker.images = []

        runner = AgentRunner(browser_enabled=True)
        await runner.initialize_webarena_env(task_id)

        result = await runner.run_task_generic(
            eval_mode=True,
            goal=runner.obs.get("goal", ""),
        )

        elapsed = time.time() - start
        answer = result.answer if result else ""
        n_actions = result.number_of_actions if result else 0

        print(f"Task {task_id}: answer={answer[:80]}, "
              f"actions={n_actions}, time={elapsed:.1f}s")

        return {
            "task_id": task_id,
            "answer": answer,
            "number_of_actions": n_actions,
            "duration_s": round(elapsed, 2),
            "error": None,
        }

    except Exception as e:
        elapsed = time.time() - start
        print(f"Task {task_id}: ERROR - {e} ({elapsed:.1f}s)")
        return {
            "task_id": task_id,
            "answer": "",
            "number_of_actions": 0,
            "duration_s": round(elapsed, 2),
            "error": str(e),
        }


async def main():
    parser = argparse.ArgumentParser(description="Run CUGA on WebArena tasks")
    parser.add_argument("--task-ids", nargs="+", type=int, help="Specific task IDs")
    parser.add_argument("--random", type=int, help="Run N random tasks (from 0-811)")
    parser.add_argument("--output", "-o", default="cuga_results.json", help="Output file")
    args = parser.parse_args()

    if args.task_ids:
        task_ids = args.task_ids
    elif args.random:
        task_ids = sorted(random.sample(range(812), args.random))
    else:
        task_ids = [211]

    print(f"CUGA WebArena Test")
    print(f"Tasks: {task_ids}")
    print(f"Model: {os.environ.get('MODEL_NAME', 'unknown')}")

    # Load expected answers for comparison
    expected = load_expected_answers()

    results = []
    for task_id in task_ids:
        result = await run_single_task(task_id)
        # Check answer against expected
        cfg = expected.get(task_id, {})
        match = check_answer(result.get("answer", ""), cfg)
        result["correct"] = match["correct"]
        result["match_reason"] = match["reason"]
        result["intent"] = cfg.get("intent", "")
        result["sites"] = cfg.get("sites", [])
        result["eval_types"] = cfg.get("eval", {}).get("eval_types", [])

        status = "CORRECT" if match["correct"] else "WRONG" if match["correct"] is False else "MANUAL"
        print(f"  → {status}: {match['reason']}")

        results.append(result)

    # Summary
    total = len(results)
    correct = sum(1 for r in results if r["correct"] is True)
    wrong = sum(1 for r in results if r["correct"] is False)
    manual = sum(1 for r in results if r["correct"] is None)
    errors = sum(1 for r in results if r["error"])

    print(f"\n{'='*60}")
    print(f"Results: {correct}/{total} correct ({correct/total*100:.0f}%)")
    print(f"  Correct: {correct}")
    print(f"  Wrong: {wrong}")
    print(f"  Manual check needed: {manual}")
    print(f"  Errors: {errors}")

    report = {
        "agent": "cuga-agent",
        "model": os.environ.get("MODEL_NAME", "unknown"),
        "total_tasks": total,
        "correct": correct,
        "wrong": wrong,
        "manual_check": manual,
        "accuracy": correct / total if total else 0,
        "task_ids": task_ids,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }

    with open(args.output, "w") as f:
        json.dump(report, f, indent=2)
    print(f"Report saved to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
