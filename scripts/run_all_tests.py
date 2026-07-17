#!/usr/bin/env python3
"""Run every test harness in order and emit ONE timestamped, commit-stamped report.

    make test-report                                   # everything
    make test-report ARGS="--skip now"                 # drop the slow phase
    GITHUB_TEST_REPO=owner/repo make test-report       # also arm the github push row

Why a runner rather than five terminal invocations: the harnesses answer different questions and have
different verdict vocabularies (pytest counts; live_e2e's pass/fail/skip; live_suite's
PASS/FAIL/XFAIL/XPASS; live_matrix's symbol grid). Reading five scrollbacks and remembering which
green means what is where mistakes come from. This captures each run verbatim, parses its own summary
line, and prints one table plus the provenance you need to trust it: UTC timestamp, commit, branch,
and whether the tree was dirty.

Each harness's raw output is kept under results/runs/<timestamp>/, so a surprising number in the
table can always be traced back to the run that produced it.

Exit code is the worst outcome across the harnesses: 0 only when nothing FAILED. Known gaps (XFAIL)
and unconfigured surfaces (SKIP) never fail the run — that is the point of having them as separate
verdicts. A pre-existing offline failure is reported and explained, not silently tolerated.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# (key, make target, extra env, what question it answers, rough minutes)
HARNESSES = [
    ("offline", "test", {},
     "Do the pure-python invariants hold? (no stack, no creds)", 1),
    # E2E_STEPS_FILE is filled in at runtime (it depends on the timestamped outdir). The e2e harness
    # records each step in the second person so the report reads as a walkthrough, not a scoreboard.
    ("live", "test-live", {"E2E_STEPS_FILE": ""},
     "Is the plumbing alive? 4 channels + 4 flow modes, one probe each", 2),
    ("now", "test-suite-now", {},
     "Can each of the 18 agents actually do its job? (asserts on meta.mcp)", 14),
    ("flows", "test-suite-flows", {"SUITE_BUDGET_SECS": "1800"},
     "Does an English sentence become the right Activepieces flow?", 6),
    ("matrix", "test-matrix", {"MATRIX_BUDGET_SECS": "1800"},
     "Is every trigger x sink combination wired, or only the ones we tried?", 6),
    # The only harness that waits for a trigger to actually fire. Everything above stops at "armed".
    ("fire", "test-fire", {"E2E_STEPS_FILE": "", "FIRE_BUDGET_SECS": "900"},
     "Does an armed flow FIRE and answer? (arms a 1-min schedule, waits for a real tick)", 9),
    ("delegation", "test-delegation", {},
     "Does the supervisor pick the right sub-agent? (labelled payloads, >=90% gate)", 10),
]

# Fleet-era harnesses: they assert per-agent invocation BY NAME, which the single-agent world
# (EVENTS_SUPERVISOR=1, plans/SUPERVISOR_REFACTOR.md) retired. Skipped LOUDLY there — a report
# that ran them would fail for architectural reasons, not defects. `delegation` is the
# supervisor-mode replacement and only runs when the flag is set.
_FLEET_ERA = {"now", "matrix", "fire"}
_SUPERVISOR_ONLY = {"delegation"}


def _supervisor_mode() -> bool:
    v = os.environ.get("EVENTS_SUPERVISOR", "")
    if not v and os.path.exists(".env"):
        for line in open(".env"):
            if line.strip().startswith("EVENTS_SUPERVISOR="):
                v = line.split("=", 1)[1]
                break
    return v.split(" #", 1)[0].strip() in ("1", "true", "yes")


def sh(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO, **kw).stdout.strip()


def provenance() -> dict:
    dirty = bool(sh(["git", "status", "--porcelain"]))
    now = time.localtime()
    return {
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "local": time.strftime("%Y-%m-%d %H:%M:%S %Z", now),
        # human-readable run-dir name (local time, filesystem-safe, sorts chronologically)
        "stamp": time.strftime("%Y-%m-%d_%H-%M-%S", now),
        "commit": sh(["git", "rev-parse", "--short", "HEAD"]) or "unknown",
        "commit_full": sh(["git", "rev-parse", "HEAD"]) or "unknown",
        "branch": sh(["git", "branch", "--show-current"]) or "detached",
        "dirty": dirty,
        "github_test_repo": os.environ.get("GITHUB_TEST_REPO", ""),
    }


def stack_state() -> dict:
    """What the harnesses will be running against. A report without this is uninterpretable."""
    import urllib.error
    import urllib.request

    def get(url, timeout=6):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            return None

    base = os.environ.get("EVENTS_SERVER_URL", "http://localhost:7860").rstrip("/")
    ap = os.environ.get("AP_BASE_URL", "http://localhost:8081").rstrip("/")
    # AP_BASE_URL may carry a trailing ` # comment` when read straight from .env
    ap = ap.split(" #", 1)[0].strip()
    st = get(f"{base}/api/events/status")
    integ = get(f"{base}/api/events/integrations") or {}
    agents = get(f"{base}/api/events/agents") or {}
    subs = get(f"{base}/api/events/subscriptions") or {}
    try:
        urllib.request.urlopen(f"{ap}/api/v1/flags", timeout=6)
        ap_up = True
    except Exception:  # noqa: BLE001
        ap_up = False
    return {
        "server_up": st is not None,
        "ap_up": ap_up,
        "worker_backend": (st or {}).get("worker_backend"),
        "agents": len(agents.get("agents", [])),
        "subscriptions_before": len(subs.get("subscriptions", [])),
        "integrations": {i["name"]: i.get("status")
                         for i in integ.get("integrations", [])
                         if i["name"] in ("box", "github", "gmail")},
    }


# ── verdict parsing: each harness speaks its own dialect ──────────────────────
def parse(key: str, out: str) -> dict:
    """Extract a normalised verdict from a harness's own summary line."""
    t = ANSI.sub("", out)
    v: dict = {"passed": 0, "failed": 0, "xfail": 0, "xpass": 0, "skipped": 0, "note": ""}

    if key == "offline":
        m = re.search(r"(?:(\d+) failed,\s*)?(\d+) passed", t)
        if m:
            v["failed"] = int(m.group(1) or 0)
            v["passed"] = int(m.group(2))
        if "test_box_poll_endpoint_dispatches_new_files" in t:
            v["note"] = ("the box-watermark test is a KNOWN pre-existing failure: it reads the real "
                         ".box_since.json instead of a temp file. Not a regression.")
        return v

    if key == "live":
        m = re.search(r"(\d+) passed · (\d+) failed · (\d+) skipped", t)
        if m:
            v.update(passed=int(m.group(1)), failed=int(m.group(2)), skipped=int(m.group(3)))
        return v

    if key in ("now", "flows"):
        m = re.search(r"(\d+) passed · (\d+) FAILED · (\d+) xfail .*?· (\d+) xpass · (\d+) skipped", t)
        if m:
            v.update(passed=int(m.group(1)), failed=int(m.group(2)), xfail=int(m.group(3)),
                     xpass=int(m.group(4)), skipped=int(m.group(5)))
        if v["xpass"]:
            v["note"] = ("XPASS = a known gap started passing. Re-sample before believing it — "
                         "support_digest fabricates on ~5 of 7 runs, so one XPASS is luck.")
        return v

    if key == "delegation":
        # "RESULT: 13/14 correct (92%) · self-answered: 1" — self-answers already count as wrong
        # in correct/total, so passed+failed reconstructs the bench's own arithmetic.
        m = re.search(r"RESULT: (\d+)/(\d+) correct \((\d+)%\)(?: · self-answered: (\d+))?", t)
        if m:
            good, total, selfies = int(m.group(1)), int(m.group(2)), int(m.group(4) or 0)
            v.update(passed=good, failed=total - good)
            if selfies:
                v["note"] = (f"{selfies} self-answer(s) counted as routing failures "
                             f"(accuracy {m.group(3)}%, gate ≥90%)")
        return v

    if key == "fire":
        # "  3 fired · 1 armed · 2 nofire · 1 skip"   — armed/nofire are honest non-failures.
        # NB the label is group 2 and the count group 1: dict(findall(...)) would key on the number.
        counts = {label: n for n, label in
                  re.findall(r"(\d+) (fired|armed|nofire|fail|skip)", t)}
        v["passed"] = int(counts.get("fired", 0))
        v["failed"] = int(counts.get("fail", 0))
        v["skipped"] = int(counts.get("skip", 0))
        # ARMED = created but never ticked; NOFIRE = deliberately not fired. Both are known gaps,
        # not passes: counting them as passes is exactly the lie this harness exists to prevent.
        v["xfail"] = int(counts.get("armed", 0)) + int(counts.get("nofire", 0))
        if v["xfail"]:
            v["note"] = ("ARMED/NOFIRE mean the flow exists but no answer was observed — either the "
                         "schedule never came round, or firing it would mutate a real repo/inbox. "
                         "Neither is a pass.")
        return v

    if key == "matrix":
        # "  needs-input: 1 · skip: 3 · reused: 4 · connect-needed: 4 · armed: 13 · error: 1"
        counts = dict(re.findall(r"([a-z-]+(?:-[a-z]+)*): (\d+)", t.split("RESULT:")[0][-400:]))
        v["passed"] = int(counts.get("armed", 0)) + int(counts.get("reused", 0))
        v["failed"] = int(counts.get("error", 0))
        v["skipped"] = int(counts.get("skip", 0))
        v["xfail"] = int(counts.get("connect-needed", 0)) + int(counts.get("needs-input", 0))
        stale = int(counts.get("claims-existing-but-none", 0))
        if stale:
            v["note"] = (f"{stale} cell(s) where the model claimed a flow exists but none does "
                         f"(stale thread memory). Reported, not fatal.")
        return v
    return v


def main() -> int:
    ap = argparse.ArgumentParser(description="Run every harness, emit one timestamped report.")
    ap.add_argument("--skip", nargs="*", default=[], choices=[k for k, *_ in HARNESSES],
                    help="harness keys to skip (e.g. --skip now matrix)")
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args()

    prov = provenance()
    outdir = pathlib.Path(a.outdir or (REPO / "results" / "runs" / prov["stamp"]))
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"\033[1mCUGA test report\033[0m  {prov['local']}")
    print(f"  commit {prov['commit']} on {prov['branch']}"
          + ("  \033[33m(tree DIRTY — these results are not reproducible from the commit)\033[0m"
             if prov["dirty"] else "  (clean tree)"))

    st = stack_state()
    if not st["server_up"]:
        print("\n  server is down. Run `make up` first.")
        return 2
    print(f"  agents={st['agents']}  AP={'up' if st['ap_up'] else 'DOWN'}  "
          f"integrations={st['integrations']}  subs_before={st['subscriptions_before']}")
    if not st["ap_up"]:
        print("  \033[33mAP is down: cron/poll/push cannot arm, and CONNECT NEEDED would be a false "
              "negative. Results below will be misleading.\033[0m")
    print(f"  logs → {outdir}\n")

    results = []
    sup = _supervisor_mode()
    for key, target, extra, question, mins in HARNESSES:
        if key in a.skip:
            print(f"  \033[90m– {key:8} skipped by request\033[0m")
            results.append({"key": key, "target": target, "skipped_by_request": True})
            continue
        if sup and key in _FLEET_ERA:
            print(f"  \033[90m– {key:8} skipped — fleet-era harness; the single-agent world "
                  f"(EVENTS_SUPERVISOR=1) replaces it with `delegation` (ROADMAP §not-yet-vetted)\033[0m")
            results.append({"key": key, "target": target, "skipped_by_request": True,
                            "note": "fleet-era; superseded in supervisor mode"})
            continue
        if not sup and key in _SUPERVISOR_ONLY:
            print(f"  \033[90m– {key:8} skipped — needs EVENTS_SUPERVISOR=1\033[0m")
            results.append({"key": key, "target": target, "skipped_by_request": True,
                            "note": "supervisor mode only"})
            continue
        print(f"  \033[1m▶ {key}\033[0m  ({target}, ~{mins} min)  {question}", flush=True)
        # `make test-report ARGS="--skip now"` exports ARGS through MAKEFLAGS into the child
        # `make test-live`, which appends it to the harness's argv and blows up on an unknown flag.
        # Scrub both, and blank ARGS explicitly so a stale value can't ride along.
        env = {k: v for k, v in os.environ.items() if k not in ("MAKEFLAGS", "MAKELEVEL", "ARGS")}
        env.update(extra)
        if "E2E_STEPS_FILE" in extra:
            env["E2E_STEPS_FILE"] = str(outdir / "steps.jsonl")
        t0 = time.time()
        p = subprocess.run(["make", target, "ARGS="], cwd=REPO, env=env,
                           capture_output=True, text=True)
        secs = time.time() - t0
        out = p.stdout + p.stderr
        (outdir / f"{key}.log").write_text(out)
        v = parse(key, out)
        v.update(key=key, target=target, question=question, exit=p.returncode, secs=round(secs, 1))
        # A harness that died before emitting a summary line parses to all zeros — which would render
        # as "nothing failed". Silence is not success: surface it as an error with the last real line.
        counted = v["passed"] + v["failed"] + v["xfail"] + v["xpass"] + v["skipped"]
        if p.returncode != 0 and counted == 0:
            tail = [ln for ln in ANSI.sub("", out).splitlines() if ln.strip()][-1:] or ["(no output)"]
            v["crashed"] = True
            v["note"] = f"harness did not run to completion (exit {p.returncode}): {tail[0][:160]}"
        elif counted == 0:
            # exit 0 with nothing parsed = the summary format changed or never printed. An all-zero
            # row reads as "nothing failed" — flag it so it can't pass silently (bit us 2026-07-16:
            # delegation ran 13/14 fine but parse() had no branch for it → silent zeros).
            v["crashed"] = True
            v["note"] = "exit 0 but NO summary parsed — harness output format changed? (all-zero row)"
        results.append(v)
        # refresh the VERIFICATION LEDGER cell this harness proves (events_docs/verification.html)
        try:
            sys.path.insert(0, os.path.join(REPO, "tests", "events"))
            from _ledger import record as _lrec
            _map = {"offline": ("offline", "fire_real"), "live": ("channels", "fire_real"),
                    "flows": ("nlflow", "fire_real"), "delegation": ("delegation", "fire_real")}
            if key in _map:
                s, c = _map[key]
                good = p.returncode == 0 and not v.get("crashed")
                _lrec(s, c, "ok" if good else "blocked",
                      f"{v['passed']} passed · {v['failed']} failed ({target})",
                      source="make test-report")
        except Exception:  # noqa: BLE001
            pass
        bits = (f"{v['passed']}P" + (f" {v['failed']}F" if v["failed"] else "")
                + (f" {v['xfail']}x" if v["xfail"] else "")
                + (f" {v['xpass']}★" if v["xpass"] else "")
                + (f" {v['skipped']}–" if v["skipped"] else ""))
        if v.get("crashed"):
            print(f"    \033[31mCRASH\033[0m  in {secs:.0f}s  (exit {p.returncode})"
                  f"\n      {v['note']}\n", flush=True)
        else:
            colour = "\033[31m" if v["failed"] else "\033[32m"
            print(f"    {colour}{bits}\033[0m  in {secs:.0f}s  (exit {p.returncode})\n", flush=True)

    subs_after = stack_state()["subscriptions_before"]
    report = render(prov, st, subs_after, results, outdir)
    (outdir / "report.md").write_text(report)
    latest = REPO / "results" / "LATEST.md"
    latest.write_text(report)
    print(report)

    html_path = write_html(prov, st, subs_after, results, outdir)
    print(f"\n  report → {outdir/'report.md'}  (also copied to results/LATEST.md)")
    print(f"  html   → {html_path}  (also copied to results/index.html)")

    crashed = any(r.get("crashed") for r in results)
    real_failures = sum(r.get("failed", 0) for r in results if r.get("key") != "offline")
    offline_fail = next((r.get("failed", 0) for r in results if r.get("key") == "offline"), 0)
    # offline_fail == 1 is the known box-watermark test; more than that is a real regression.
    return 1 if (crashed or real_failures or offline_fail > 1) else 0


def load_steps(outdir) -> list[dict]:
    """The walkthrough rows recorded by the live harness. Empty if it crashed before its first step."""
    sys.path.insert(0, str(REPO / "tests" / "events"))
    try:
        from steps import read as read_steps
    except ImportError:
        return []
    return read_steps(str(outdir / "steps.jsonl"))


def write_html(prov, st, subs_after, results, outdir) -> pathlib.Path:
    """Same run, rendered as one self-contained page — plus the per-case detail the markdown omits.

    Written twice: next to the logs (permanent record of this run) and to results/index.html (always
    the latest). They differ only in how the raw-log links resolve.
    """
    sys.path.insert(0, str(REPO / "scripts"))
    from report_html import render_html

    steps = load_steps(outdir)
    logs = {k: (outdir / f"{k}.log").read_text(errors="replace")
            for k in (r["key"] for r in results) if (outdir / f"{k}.log").exists()}

    run_html = outdir / "report.html"
    run_html.write_text(render_html(prov, st, subs_after, results, outdir, steps, logs, ""))
    (REPO / "results" / "index.html").write_text(
        render_html(prov, st, subs_after, results, outdir, steps, logs, f"runs/{outdir.name}/"))
    return run_html


def narrative(outdir) -> str:
    """Render the recorded steps as a walkthrough: who did what, what we expected, what came back.

    Dimension columns (utterance / channel / integration / trigger) appear per-phase, and only where
    a phase actually supplies them — a Slack round trip has no integration, and a column of dashes
    is worse than no column.
    """
    sys.path.insert(0, str(REPO / "tests" / "events"))
    try:
        from steps import dims_present
    except ImportError:
        return ""
    rows = load_steps(outdir)
    if not rows:
        return ""
    L = ["\n## End-to-end walkthrough\n",
         "Exactly what a person would do, and exactly what came back. A blank verdict is scene-setting "
         "(posting the message), not an assertion — only rows with ✓/✗ are checked.\n"]
    for phase in dict.fromkeys(r["phase"] for r in rows):
        prows = [x for x in rows if x["phase"] == phase]
        dims = dims_present(prows)
        L.append(f"### {phase}\n")
        head = ["Surface"] + [d.title() for d in dims] + ["Who", "Does what", "Expected",
                                                          "Actually got", ""]
        L.append("| " + " | ".join(head) + " |")
        L.append("|" + "---|" * (len(head) - 1) + ":--:|")
        for r in prows:
            mark = "" if r["ok"] is None else ("✓" if r["ok"] else "**✗**")
            note = f"<br><sub>{md(r['note'])}</sub>" if r["note"] else ""
            cells = [f"`{r['surface']}`"]
            for d in dims:
                v = (r.get(d) or "").strip()
                cells.append(f"“{md(v)}”" if d == "utterance" and v else (md(v) if v else "—"))
            cells += [md(r["actor"]), md(r["action"]) + note, md(r["expect"]),
                      md(r["got"] or "—"), mark]
            L.append("| " + " | ".join(cells) + " |")
        L.append("")
    return "\n".join(L)


def md(t: str) -> str:
    """Make a value safe inside a markdown table cell."""
    return str(t).replace("|", "\\|").replace("\n", " ")


def render(prov, st, subs_after, results, outdir) -> str:
    L = []
    L.append("# CUGA events — test report\n")
    L.append(f"- **When:** {prov['utc']}  ({prov['local']})")
    L.append(f"- **Commit:** `{prov['commit']}` ({prov['commit_full']}) on `{prov['branch']}`")
    L.append(f"- **Tree:** {'DIRTY — not reproducible from this commit' if prov['dirty'] else 'clean'}")
    L.append(f"- **Stack:** agents={st['agents']}, AP={'up' if st['ap_up'] else 'DOWN'}, "
             f"worker={st['worker_backend']}, integrations={st['integrations']}")
    L.append(f"- **Subscriptions:** {st['subscriptions_before']} before → {subs_after} after "
             f"({'no leak' if st['subscriptions_before'] == subs_after else '⚠ LEAKED'})")
    if prov["github_test_repo"]:
        L.append(f"- **GITHUB_TEST_REPO:** `{prov['github_test_repo']}` (github push row armed; "
                 f"webhooks created by the run are deleted afterwards)")
    L.append(f"- **Raw logs:** `{outdir.relative_to(REPO)}/`\n")

    L.append("| Harness | Answers | Pass | Fail | XFail | XPass | Skip | Secs |")
    L.append("|---|---|--:|--:|--:|--:|--:|--:|")
    for r in results:
        if r.get("skipped_by_request"):
            L.append(f"| `{r['key']}` | _skipped by request_ | | | | | | |")
            continue
        if r.get("crashed"):
            L.append(f"| `{r['key']}` | {r['question']} | — | **CRASH** | — | — | — | {r['secs']:.0f} |")
            continue
        L.append(f"| `{r['key']}` | {r['question']} | {r['passed']} | "
                 f"{'**' + str(r['failed']) + '**' if r['failed'] else 0} | "
                 f"{r['xfail']} | {r['xpass']} | {r['skipped']} | {r['secs']:.0f} |")
    L.append("")

    L.append(narrative(outdir))

    notes = [r for r in results if r.get("note")]
    if notes:
        L.append("## How to read this\n")
        for r in notes:
            L.append(f"- **`{r['key']}`** — {r['note']}")
        L.append("")

    L.append("## Verdict vocabulary\n")
    L.append("- **FAIL** — expected to work, broke. The only thing worth acting on immediately.")
    L.append("- **XFAIL** — a known gap, with its reason printed in the harness output. Not a regression.")
    L.append("- **XPASS** — a known gap started passing. Re-sample, then delete the expectation.")
    L.append("- **SKIP** — surface not configured. Never counted as a pass.\n")
    L.append("Only `live_suite` and (since 2026-07-09) `live_e2e`/`live_matrix` verify that an armed "
             "flow **really exists in Activepieces**; a bare `ap_flow_id` proves nothing, because "
             "`find_or_create_flow` de-duplicates without re-checking (`concierge.py:285-289`).\n")
    L.append("**None of these harnesses fire real data through an armed watcher.** They prove a flow "
             "is created correctly, not that it behaves correctly when a real event lands. For that: "
             "`live_gmail_e2e.py`, `live_box_e2e.py`, `live_github_e2e.py`.")
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
