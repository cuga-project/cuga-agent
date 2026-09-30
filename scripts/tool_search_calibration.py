#!/usr/bin/env python3
"""Calibrate tool_search's keyword-stage confidence gate on the M3/VAKRA REST catalogue.

Ground truth without labels: every M3 REST endpoint (``vakra/environment/m3/rest/server/*.py``)
carries a natural-language comment written independently of its OpenAPI ``summary``::

    # Endpoint to get the first and last names of players born in a specific year and not in a specific country
    @app.get("/v1/hockey/...", operation_id="get_players_by_birth_year_and_country", summary="Retrieve ...")

The comment is the query, the endpoint is the gold tool, and the pool is the endpoint's
own domain (M3 runs each domain as one app), so pools are realistic in size. The servers
are parsed with ``ast`` — never imported, so no databases are needed.

Reports keyword recall@1/@5, then grid-searches ``min_score`` x ``margin_ratio`` for the gate
(``helpers.tool_search._is_confident``): how often the keyword stage answers alone, and how
often the gold tool is in its top-k when it does. Usage::

    uv run python scripts/tool_search_calibration.py \\
        --server-dir ../cuga-eval/vendor/vakra/environment/m3/rest/server [--k 5] [--json out.json]

See docs/design/fc-deferred-tool-search.md §7.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Tuple

from cuga.backend.cuga_graph.nodes.cuga_lite.helpers.tool_search import ToolSearchConfig, _is_confident
from cuga.backend.cuga_graph.nodes.cuga_lite.shortlister import keyword

_COMMENT_PREFIX = re.compile(r"^#\s*(endpoint\s+(to|for)\s+)?", re.IGNORECASE)


@dataclass
class Case:
    domain: str
    query: str
    gold: str


def _kw(call: ast.Call, name: str) -> Optional[str]:
    for k in call.keywords:
        if k.arg == name and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
            return k.value.value
    return None


def parse_server(path: Path) -> Tuple[List[SimpleNamespace], List[Case]]:
    """Tools (keyword-rankable namespaces) and (comment → endpoint) cases for one domain."""
    domain = path.stem
    source = path.read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    tools: List[SimpleNamespace] = []
    cases: List[Case] = []
    with warnings.catch_warnings():  # third-party servers carry invalid escape sequences
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                continue
            if dec.func.attr not in ("get", "post", "put", "delete", "patch"):
                continue
            operation_id = _kw(dec, "operation_id") or node.name
            summary = _kw(dec, "summary") or ""
            name = f"{domain}_{operation_id}"
            tools.append(
                SimpleNamespace(
                    name=name,
                    # FastAPI omits ``description`` without a docstring, and the registry
                    # parser falls back to ``summary`` — so both carry it.
                    description=summary,
                    _search_hint=summary,
                    _operation_id=operation_id,
                    _app_name=domain,
                )
            )
            first = min([dec.lineno] + [d.lineno for d in node.decorator_list]) - 2  # 0-based line above
            if 0 <= first < len(lines) and lines[first].lstrip().startswith("#"):
                query = _COMMENT_PREFIX.sub("", lines[first].strip()).strip()
                if len(query.split()) >= 3:
                    cases.append(Case(domain=domain, query=query, gold=name))
            break
    return tools, cases


def load(server_dir: Path, domains: Optional[Iterable[str]] = None):
    wanted = set(domains or [])
    catalogue: Dict[str, List[SimpleNamespace]] = {}
    cases: List[Case] = []
    for path in sorted(server_dir.glob("*.py")):
        if path.stem.startswith("_") or (wanted and path.stem not in wanted):
            continue
        tools, domain_cases = parse_server(path)
        if tools:
            catalogue[path.stem] = tools
            cases.extend(domain_cases)
    return catalogue, cases


def evaluate(catalogue, cases: List[Case], k: int):
    """Per case: the ranked list (name, score) within its domain."""
    indexes = {d: {t.name: keyword.index_tool(t) for t in tools} for d, tools in catalogue.items()}
    runs = []
    for case in cases:
        ranked = keyword.rank(
            case.query, catalogue[case.domain], max_results=None, index=indexes[case.domain]
        )
        names = [n for n, _ in ranked]
        rank = names.index(case.gold) + 1 if case.gold in names else None
        runs.append((case, ranked, rank))
    return runs


def gate_grid(runs, k: int, min_scores: Iterable[int], margins: Iterable[float]):
    rows = []
    for min_score in min_scores:
        for margin in margins:
            config = ToolSearchConfig(min_score=min_score, margin_ratio=margin)
            confident = [(r, ranked) for (_, ranked, r) in runs if _is_confident(ranked, k, config)]
            hits = sum(1 for r, _ in confident if r is not None and r <= k)
            rows.append(
                {
                    "min_score": min_score,
                    "margin_ratio": margin,
                    "confident_rate": len(confident) / len(runs) if runs else 0.0,
                    "confident_precision": hits / len(confident) if confident else 0.0,
                    # Fraction of all queries answered correctly by keyword alone (no model call).
                    "free_and_correct": hits / len(runs) if runs else 0.0,
                }
            )
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--server-dir", required=True, type=Path)
    parser.add_argument("--k", type=int, default=5, help="tool_search max_results")
    parser.add_argument("--domains", nargs="*", help="restrict to these domains")
    parser.add_argument("--target-precision", type=float, default=0.9)
    parser.add_argument("--json", type=Path, help="write the full report here")
    args = parser.parse_args(argv)

    catalogue, cases = load(args.server_dir, args.domains)
    if not cases:
        print("no cases found", file=sys.stderr)
        return 1
    runs = evaluate(catalogue, cases, args.k)
    n = len(runs)
    at1 = sum(1 for _, _, r in runs if r == 1) / n
    atk = sum(1 for _, _, r in runs if r is not None and r <= args.k) / n
    pool = sum(len(t) for t in catalogue.values()) / len(catalogue)
    print(
        f"domains={len(catalogue)} tools={sum(len(t) for t in catalogue.values())} cases={n} mean_pool={pool:.0f}"
    )
    print(f"keyword recall@1={at1:.3f} recall@{args.k}={atk:.3f}")

    rows = gate_grid(runs, args.k, range(4, 31, 2), [x / 20 for x in range(0, 13)])
    ok = [r for r in rows if r["confident_precision"] >= args.target_precision]
    best = (
        max(ok, key=lambda r: r["free_and_correct"])
        if ok
        else max(rows, key=lambda r: r["confident_precision"])
    )
    print(f"\nbest gate at precision>={args.target_precision}: {json.dumps(best)}")
    defaults = ToolSearchConfig()
    current = next(
        r
        for r in rows
        if r["min_score"] == defaults.min_score and abs(r["margin_ratio"] - defaults.margin_ratio) < 1e-9
    )
    print(
        f"current defaults ({defaults.min_score}, {defaults.margin_ratio}):            {json.dumps(current)}"
    )
    print("\nmin_score margin_ratio confident_rate confident_precision free_and_correct")
    for r in sorted(rows, key=lambda r: -r["free_and_correct"])[:12]:
        print(
            f"{r['min_score']:>9} {r['margin_ratio']:>12.2f} {r['confident_rate']:>14.3f} "
            f"{r['confident_precision']:>19.3f} {r['free_and_correct']:>16.3f}"
        )

    if args.json:
        misses = [
            {
                "domain": c.domain,
                "query": c.query,
                "gold": c.gold,
                "rank": r,
                "top": [x for x, _ in ranked[:3]],
            }
            for c, ranked, r in runs
            if r is None or r > args.k
        ]
        args.json.write_text(
            json.dumps(
                {
                    "recall_at_1": at1,
                    f"recall_at_{args.k}": atk,
                    "grid": rows,
                    "best": best,
                    "misses": misses,
                },
                indent=2,
            )
        )
        print(f"\nwrote {args.json} ({len(misses)} misses)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
