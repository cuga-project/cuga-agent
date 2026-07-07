# Box setup (Activepieces backend — the default)

Box is an **integration**, and integrations default to **Activepieces**: AP watches the folder
(`new_file` trigger) with the connected OAuth token and fires `/invoke`. This is what the concierge
arms when you say *"when a resume lands in my Box…"* (`create_push_flow`).

```
new file in Box ─▶ AP new_file trigger (OAuth) ─▶ /invoke (resume_judge) ─▶ deliver
```

> **Opt-in direct poll** (`EVENTS_BOX_BACKEND=direct` + `BOX_DEV_TOKEN`): CUGA lists the folder via
> `POST /api/events/box/poll` — no OAuth app, but you drive/schedule the poll yourself. Handy for a
> quick, AP-free test; see the bottom of this guide. (We keep both, symmetric with Slack's parked AP
> path — the *default* for integrations is AP.)

## What you'll need (AP default)
- A Box **OAuth 2.0 app** (client id/secret + a saved redirect URI) and Activepieces running.
- Each user logs in via `GET /api/events/connect/box` (per-user) — AP holds + refreshes the token.

## What you'll need (direct opt-in)
- A Box account (free is fine for the **direct** path).
- A Box **Developer Token** — a ~60-minute token you generate on demand; no OAuth app config.

## Steps — the DIRECT path (`EVENTS_BOX_BACKEND=direct`, AP-free)
These steps set up the **opt-in direct poll** (fastest for a test). For the **AP OAuth default**
instead: register the Box OAuth app above, set `EVENTS_OAUTH_BOX_CLIENT_ID/_SECRET`, have each user
run `GET /api/events/connect/box` (consent → AP holds the token), then the concierge arms the
`new_file` **push** flow. The numbered steps below are the direct path only.

1. **Create a Box app (for the token)** — <https://app.box.com/developers/console> → **Create New
   App** → *Custom App* → *User Authentication (OAuth 2.0)*. (You won't configure OAuth — you only
   need the app to mint a dev token.)

2. **Generate a Developer Token** — in the app's **Configuration** tab → **Generate Developer
   Token**. It lasts ~60 minutes; regenerate when it expires.

3. **Add to `.env`**:
   ```
   BOX_DEV_TOKEN=…            # or EVENTS_BOX_TOKEN=… for a longer-lived access token later
   ```
   Restart the server.

4. **Find the folder id** — open the folder in Box; the URL ends with the folder id
   (`https://app.box.com/folder/<FOLDER_ID>`). The root "All Files" is `0`.

5. **Poll it** — the watcher lists new files and fires `resume_judge` on each:
   ```bash
   GW=$(grep '^GATEWAY_TOKEN=' .env | cut -d= -f2- | sed 's/ *#.*//' | tr -d ' "')
   curl -s -X POST localhost:8100/api/events/box/poll \
        -H "content-type: application/json" -H "X-Gateway-Token: $GW" \
        -d '{"folder_id":"<FOLDER_ID>","since":null,"agent":"resume_judge","deliver_to":"slack"}'
   # → {"ok":true,"processed":[{"id":"9","name":"resume.pdf"}],"newest":"2026-07-06T11:00:00-07:00"}
   ```
   Store `newest` and pass it as `since` next time so you only process new files. A schedule/cron can
   drive this endpoint on an interval.

## Verify
```bash
.venv/bin/python tests/events/preflight.py box                       # dev token valid (users/me)
BOX_FOLDER_ID=0 EVENTS_SERVER_URL=http://localhost:8100 \
  .venv/bin/python tests/events/live_box_direct_check.py             # whoami → list → poll, end to end
```

## Known limitation (follow-up)
The watcher currently passes the file **name** to `resume_judge`, not its **content** — so the agent
can reason about the drop but can't yet read the file's bytes (you'll see `cuga_text_extract_text →
File not found`). Feeding the agent the Box file content (download in `box_direct`, or a Box-read MCP
tool) is the next step to a true resume judgment. The trigger→dispatch→deliver plumbing is verified.

## Troubleshooting
- **`box/poll` → 401** — missing/invalid `X-Gateway-Token` (it's gateway-protected like `/invoke`).
- **`box/poll` → 502 "Box list folder … HTTP 401"** — the dev token expired (~60 min). Regenerate it
  in the Box console, update `.env`, restart. (Deliberately loud — a stale token is never silent.)
- **preflight box ❌** — same: expired/empty `BOX_DEV_TOKEN`.
