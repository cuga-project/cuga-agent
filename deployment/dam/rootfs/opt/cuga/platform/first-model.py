"""Print the model CUGA runs on when the agent names none, or nothing.

DAM's Model Router lists, for an image's own harness, every model the
agent's providers list, with the platform's preferred work model first.
CUGA speaks Chat Completions, so this takes the first model that serves it.
"""

import json
import sys
import urllib.request

request = urllib.request.Request(sys.argv[1].rstrip("/") + "/v1/models")
try:
    with urllib.request.urlopen(request, timeout=15) as response:
        models = json.load(response).get("data") or []
except (OSError, ValueError):
    models = []
for model in models:
    if not isinstance(model, dict) or not model.get("id"):
        continue
    dialects = (model.get("metadata") or {}).get("dialects") or ["openai-chat"]
    if "openai-chat" in dialects:
        print(model["id"])
        break
