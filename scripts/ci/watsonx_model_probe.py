#!/usr/bin/env python3
"""Pick the watsonx model for a CI run: gpt-oss-120b, or a fallback when gpt-oss-120b truncates replies.

Background
----------
Since 15 Sep, watsonx.ai us-south has at times returned gpt-oss-120b chat replies with
``content`` cut at the first line break (#784, ml-planning 62316). The reply keeps HTTP 200,
``finish_reason: stop`` and a normal ``completion_tokens`` count; only the narration sentence
before the ``python`` code block survives. CugaLite then finalizes on that sentence and the
live-LLM tests fail. The truncation comes and goes with watsonx deployments, so CI checks for
it at the start of each run instead of pinning a model.

What it does
------------
Sends ``watsonx_probe_request.json`` (a captured CugaLite first turn: its system prompt and
"Get my top account from digital sales") to the primary model ``PROBE_REQUESTS`` times. A
healthy reply is one sentence, a line break, then a code block. A truncated reply is a single
line with no code block and ``finish_reason: stop``. If any reply is truncated, or at least
half the requests fail, and the fallback model answers, the fallback is chosen.

Output
------
Writes ``model=<name>`` to ``$GITHUB_OUTPUT``: the fallback name when switching, empty
otherwise, so a workflow can use ``needs.<job>.outputs.model || secrets.MODEL_NAME`` (or
``steps.<id>.outputs.model`` inside one job). It always exits 0: a probe that cannot run keeps the primary
model rather than blocking CI.

Uses only the standard library so the probe job needs no ``uv sync``.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

PRIMARY_MODEL = "openai/gpt-oss-120b"
# Chosen 30 Sep: passed the approval, policy e2e and SDK suites where gpt-oss-120b truncated;
# similar latency. It is also the watsonx default in src/cuga/backend/llm/models.py.
FALLBACK_MODEL = "meta-llama/llama-4-maverick-17b-128e-instruct-fp8"
PROBE_REQUESTS = 24
PROBE_CONCURRENCY = 8
REQUEST_TIMEOUT_S = 90
CHAT_API_VERSION = "2025-02-11"
REQUEST_FILE = Path(__file__).with_name("watsonx_probe_request.json")


@dataclass
class Reply:
    seconds: float
    content: str = ""
    finish_reason: str | None = None
    completion_tokens: int | None = None
    error: str | None = None

    @property
    def truncated(self) -> bool:
        text = self.content.strip()
        return self.error is None and self.finish_reason == "stop" and "```" not in text and "\n" not in text


def iam_token(api_key: str) -> str:
    data = urllib.parse.urlencode(
        {"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": api_key}
    ).encode()
    req = urllib.request.Request(
        "https://iam.cloud.ibm.com/identity/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)["access_token"]


def chat(url: str, token: str, body: dict) -> Reply:
    req = urllib.request.Request(
        f"{url}/ml/v1/text/chat?version={CHAT_API_VERSION}",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:
            payload = json.load(resp)
        choice = payload["choices"][0]
        return Reply(
            time.monotonic() - start,
            content=choice["message"].get("content") or "",
            finish_reason=choice.get("finish_reason"),
            completion_tokens=payload.get("usage", {}).get("completion_tokens"),
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError) as exc:
        return Reply(time.monotonic() - start, error=type(exc).__name__ + ": " + str(exc)[:200])


def request_body(model: str, project_id: str) -> dict:
    messages = json.loads(REQUEST_FILE.read_text())["messages"]
    return {
        "model_id": model,
        "project_id": project_id,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 4000,
    }


def write_github_file(var: str, line: str) -> None:
    path = os.environ.get(var)
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--primary", default=os.environ.get("MODEL_NAME") or PRIMARY_MODEL)
    parser.add_argument("--fallback", default=FALLBACK_MODEL)
    parser.add_argument("--requests", type=int, default=PROBE_REQUESTS)
    args = parser.parse_args()

    api_key = os.environ.get("WATSONX_APIKEY")
    project_id = os.environ.get("WATSONX_PROJECT_ID")
    url = (os.environ.get("WATSONX_URL") or "").rstrip("/")
    if not (api_key and project_id and url):
        print("watsonx credentials not set; keeping the configured model")
        write_github_file("GITHUB_OUTPUT", "model=")
        return 0

    try:
        token = iam_token(api_key)
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(
            f"::warning::watsonx model probe could not get an IAM token ({type(exc).__name__}); keeping the configured model"
        )
        write_github_file("GITHUB_OUTPUT", "model=")
        return 0

    body = request_body(args.primary, project_id)
    with concurrent.futures.ThreadPoolExecutor(PROBE_CONCURRENCY) as pool:
        replies = list(pool.map(lambda _: chat(url, token, body), range(args.requests)))

    truncated = [r for r in replies if r.truncated]
    errors = [r for r in replies if r.error]
    for r in replies:
        status = "ERROR" if r.error else "TRUNCATED" if r.truncated else "ok"
        detail = r.error or repr(r.content[:90])
        print(f"{r.seconds:5.1f}s {status:9} tokens={r.completion_tokens} len={len(r.content)} {detail}")
    latencies = sorted(r.seconds for r in replies if not r.error) or [0.0]
    verdict = (
        f"{args.primary}: {len(truncated)}/{len(replies)} truncated, {len(errors)} errors, "
        f"p50 {latencies[len(latencies) // 2]:.1f}s"
    )
    print(verdict)

    unhealthy = truncated or len(errors) * 2 >= len(replies)
    chosen = args.primary
    if unhealthy:
        check = chat(url, token, request_body(args.fallback, project_id))
        if check.error is None and check.content.strip():
            chosen = args.fallback
            print(f"::warning::{verdict}. Using {args.fallback} for this run (#784).")
        else:
            print(
                f"::warning::{verdict}. Fallback {args.fallback} also failed ({check.error}); keeping {args.primary}."
            )

    write_github_file("GITHUB_OUTPUT", f"model={chosen if chosen != args.primary else ''}")
    write_github_file(
        "GITHUB_STEP_SUMMARY",
        f"### watsonx model probe\n\n- {verdict}\n- Model for this run: `{chosen}`\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
