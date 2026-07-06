# GitHub setup (Activepieces backend)

GitHub is an **integration**, so it runs on **Activepieces**: AP watches your repo (a `new_pull_request`
or `new_issue` trigger, using your token) and fires `/invoke`. The concierge arms this when you say
*"when a PR opens on my repo…"* (`create_push_flow`). GitHub connects with a **Personal Access Token**
(PAT) — no OAuth consent flow needed (token auth, simpler than Box/Gmail).

```
new PR / issue ─▶ AP github trigger (your PAT) ─▶ /invoke (pr_reviewer) ─▶ deliver (any channel)
```

The seeded **`pr_reviewer`** agent summarizes a PR and flags risks (uses `cuga-code` + `cuga-text`).

## What you'll need
- A GitHub **Personal Access Token** with `repo` scope (and `read:org` if you watch an org repo).
- Activepieces running + reachable (for the webhook, a public URL — `EVENTS_PUBLIC_URL`).

## Steps
1. **Create a PAT** — GitHub → *Settings → Developer settings → Personal access tokens* →
   *Tokens (classic)* → generate with **`repo`** scope (+ `read:org` for org repos). Copy it.

2. **Connect it** (token auth — no consent redirect):
   ```bash
   curl -s -X POST localhost:8100/api/events/connect/github/token \
        -H "content-type: application/json" -H "x-user-id: admin" \
        -d '{"token":"ghp_…","ownership":"per_user"}'
   # → {"ok":true,"app":"github","connection":"ea::…::github"}
   ```
   *(Or paste it in the Studio → Integrations → GitHub → Connect.)* Ownership: `per_user` (each user
   their own) or `tenant` (shared).

3. **Arm a watcher** — ask the concierge:
   *"when a pull request opens on my repo, summarize it and message me."* → it arms an AP push flow
   (`pr_reviewer`, `github` source). AP registers a webhook on the repo.

## Verify
```bash
.venv/bin/python tests/events/preflight.py            # (add a github check if desired)
# full integration e2e (NOW/CRON/POLL + PUSH box/github/gmail):
GATEWAY_TOKEN=<from .env> EVENTS_SERVER_URL=http://localhost:8100 \
  .venv/bin/python tests/events/live_integrations_e2e.py
```
With the PAT connected, the `PUSH · github` leg **arms a real AP flow** (an `ap_flow_id` appears on the
subscription). Then open a PR on the watched repo → a summary is delivered.

## Troubleshooting
- **`CONNECT NEEDED — connect your github`** — no PAT connected yet; run step 2.
- **Webhook never fires** — AP needs a public URL (`EVENTS_PUBLIC_URL`) reachable by GitHub; confirm
  the repo's *Settings → Webhooks* shows AP's endpoint with recent green deliveries.
- **Why not direct?** GitHub *could* go direct (its webhooks are simple), but it still needs a public
  URL, and AP already gives us the trigger + token store for free. Integrations stay on AP.
