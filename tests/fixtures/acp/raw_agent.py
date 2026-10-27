"""Intentionally invalid ACP subprocess used only by failure-path tests."""

from __future__ import annotations

import argparse
import json
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("abrupt", "malformed", "startup-timeout"))
    args = parser.parse_args()
    if args.scenario == "malformed":
        # Echo initialize's request ID but return a result that cannot satisfy
        # the ACP InitializeResponse contract.
        request = json.loads(sys.stdin.readline())
        response = {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": "invalid-acp-shape",
        }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
        time.sleep(60)
    elif args.scenario == "startup-timeout":
        time.sleep(60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
