"""Print the first Chat Completions model DAM's Model Router lists, or nothing.

The router lists models per harness, and CUGA is not one it routes, so this
asks as a harness that speaks Chat Completions through the router.
"""

import json
import sys
import urllib.request

request = urllib.request.Request(
    sys.argv[1].rstrip("/") + "/v1/models",
    headers={"X-Platform-Harness": "copilot"},
)
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        models = json.load(response).get("data") or []
except (OSError, ValueError):
    models = []
if models and isinstance(models[0], dict) and models[0].get("id"):
    print(models[0]["id"])
