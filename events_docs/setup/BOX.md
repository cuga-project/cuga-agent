# Box setup (direct backend — the default)

Box runs a **direct** poll: CUGA lists a Box folder with a token (Box's REST API takes it directly)
and fires a watcher agent on each **new** file. No Activepieces, no OAuth app, no redirect URI.

```
new file in Box folder ─▶ POST /api/events/box/poll (CUGA lists the folder)
                                          │  per new file
                                 /invoke (resume_judge)
                                          │
                          deliver to a direct channel (Slack) or the /invoke response
```

> The AP-based Box path (`new_file` **webhook** trigger) still exists, but it needs a **paid Box app**
> that can save a Redirect URI + `manage_webhook` **and** the OAuth consent flow (AP refuses a
> pre-obtained token). The direct poll sidesteps all of that — recommended.

## What you'll need
- A Box account (free is fine for the **direct** path).
- A Box **Developer Token** — a ~60-minute token you generate on demand; no OAuth app config.

## Steps

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
