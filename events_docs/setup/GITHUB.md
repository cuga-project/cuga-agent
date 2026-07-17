# GitHub setup (Activepieces backend)

GitHub is an **integration**, so it runs on **Activepieces**: AP watches your repo (a `new_pull_request`
or `new_issue` trigger) and fires `/invoke`. The concierge arms this when you say *"when a PR opens on
my repo…"* (`create_push_flow`). AP creates a real webhook on the repo, so it needs a public URL
(`EVENTS_PUBLIC_URL`).

```
new PR / issue ─▶ AP github trigger (OAuth conn) ─▶ /invoke (pr_reviewer) ─▶ deliver (any channel)
```

The seeded **`pr_reviewer`** agent summarizes a PR and flags risks (uses `cuga-code` + `cuga-text`).

## GitHub is OAuth, **not** a pasted PAT

This tripped us for a while, so it is worth being explicit. Activepieces' `@activepieces/piece-github`
accepts **only** an OAuth2 (or GitHub App) connection — check for yourself:

```bash
curl -s "$AP_BASE_URL/api/v1/pieces/@activepieces/piece-github" | jq '.auth[].type'
# → "OAUTH2"   "CUSTOM_AUTH"      (no SECRET_TEXT)
```

A PAT pasted as a `SECRET_TEXT` connection is *accepted by AP's connection store* and then **unusable
by the piece**: the flow arms fine and later fails at publish with `401 Bad credentials`, which looks
exactly like an under-scoped token. `POST /api/events/connect/github/token` now refuses a PAT with a
`400` for this reason. Connect via OAuth.

## What you'll need
- A GitHub **OAuth App** (client id + secret). *An OAuth App, not a GitHub App — adjacent pages.*
- Activepieces running + reachable, and `EVENTS_PUBLIC_URL` set (AP registers the repo webhook there).

## Steps
1. **Create an OAuth App** — GitHub → *Settings → Developer settings → OAuth Apps → New OAuth App*
   ([github.com/settings/developers](https://github.com/settings/developers)).
   - **Homepage URL**: your `EVENTS_PUBLIC_URL`
   - **Authorization callback URL**, *exactly* (GitHub does a literal compare — no trailing slash):
     `<EVENTS_PUBLIC_URL>/api/events/connect/github/callback`
   - Then **Generate a new client secret** and copy both values.

2. **Put the app creds in `.env`** and reload:
   ```bash
   EVENTS_OAUTH_GITHUB_CLIENT_ID=Iv1.xxxxxxxxxxxx
   EVENTS_OAUTH_GITHUB_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   `make reload`. (You may leave `GITHUB_TOKEN` in `.env` — nothing arms AP from it anymore; the
   live-test harnesses use it to check webhooks directly.)

3. **Consent in the browser** — each user connects their own GitHub:
   open `<EVENTS_PUBLIC_URL>/api/events/connect/github` and approve. Requested scopes: `repo` +
   `admin:repo_hook` (the second is what lets a trigger create the repo webhook).
   *(Or Studio → Integrations → GitHub → Connect — the button opens this same consent page.)*

   **Those two scopes cover ALL 14 GitHub triggers** — `new_pr` · `new_issue` · `new_star` ·
   `new_push` · `new_commit` · `new_release` · `new_branch` · `new_milestone` · `new_repo_label` ·
   `new_collaborator` · `new_discussion` · `new_discussion_comment` · `new_review_request` ·
   `new_gh_mention`. One repo webhook carries every subscribed event type (the piece disambiguates
   on `X-GitHub-Event`), so there is nothing extra to grant per trigger. Every one is
   machine-fireable end to end: `tests/events/live_github_triggers.py`.

4. **Arm a watcher** — ask the concierge, **naming the repo**:
   *"watch the repo owner/name for new pull requests and summarize each one."* → it arms an AP push
   flow (`pr_reviewer`, `github` source), and AP registers a `pull_request` webhook on the repo.

## Verify
```bash
# the connection must be OAUTH2, not SECRET_TEXT:
curl -s "$AP_BASE_URL/api/v1/app-connections?projectId=<pid>" | jq '.data[] | select(.externalId|test("github")) | .type'
# → "OAUTH2"

# a real webhook appears on the repo after arming:
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/<owner>/<name>/hooks | jq length

# fire it WITHOUT opening a real PR (a push flow's body becomes the trigger output):
curl -s -X POST "localhost:7860/api/events/subscriptions/<sub_id>/run?timeout=180" \
  -H "X-Gateway-Token: $GATEWAY_TOKEN" -H 'content-type: application/json' -d '{
    "title":"Fix race in shutdown","html_url":"https://github.com/owner/name/pull/1",
    "body":"Adds a WaitGroup and a drain timeout.",
    "base":{"repo":{"full_name":"owner/name"}},"user":{"login":"octocat"},
    "additions":42,"deletions":7,"changed_files":3}' | jq '.answer'
```
Deleting the subscription removes the webhook from the repo.

## Rotating the PAT

A GitHub PAT expires, or gets revoked, and the failure is nastier than it looks: the flow **arms
cleanly** and then fails at run time with `401 Bad credentials`, nowhere near the paste that caused it.
Look for it in the run log — `GET /api/events/runs/<id>` surfaces the failing step's error — not in the
response to the connect call.

To rotate, POST the new token. This **overwrites** the stored connection (and, if Activepieces refuses
the overwrite, deletes and recreates it):

```bash
curl -X POST localhost:7860/api/events/connect/github/token \
  -H 'content-type: application/json' -d '{"token":"ghp_NEW"}'
```

> Fixed 2026-07-09. Before that, `ensure_secret_connection` returned early whenever a connection with
> the same `externalId` existed, so pasting a fresh PAT — from the API *or* the Studio's Connect
> button — silently did nothing and AP kept using the dead token.

**Editing `GITHUB_TOKEN` in `.env` and restarting does not rotate it.** Boot-time auto-connect only
creates what is *missing*, deliberately: it must never clobber a connection you authorized by hand with
whatever stale value is sitting in the environment. Rotate through the endpoint above.

The PAT needs `admin:repo_hook` to arm a PUSH watcher — Activepieces creates a repository webhook, and
GitHub rejects that scope-less.

## Troubleshooting
- **`CONNECT NEEDED — connect your github`** — this message has **three different causes**, and only
  the first is the one it names. Check them in order:
  1. **No PAT connected yet** — run step 2.
  2. **Activepieces is down or unreachable.** The gate calls AP to ask whether the connection exists,
     and on *any* exception it assumes "not connected". A stopped AP container therefore produces an
     identical "connect your credentials" message. Check first:
     `podman ps` and `curl -s localhost:8081/api/v1/flags`.
  3. **`GITHUB_TOKEN` is in `.env` but auto-connect never landed it in AP.** On a fresh AP database
     the `@activepieces/piece-github` piece isn't installed, so the connection can't be created. Run
     `make ap-pieces`, then restart. Confirm with
     `curl -s localhost:7860/api/events/integrations` — github showing `auto_connect_pending` means
     exactly this.
- **Slack asks you to connect even though the Studio says connected** — GitHub credentials are keyed
  per `(tenant, user)` as `ea::<tenant>::<user>::github`. A Slack sender whose Slack id has never been
  account-linked falls back to the operator principal (`local`), so it looks up a *different* key than
  the one the Studio's web session created. Link the account first (`/link <token>`), or set the PAT
  in `.env` as `GITHUB_TOKEN` so auto-connect creates it under the operator principal.
- **Webhook never fires** — AP needs a public URL (`EVENTS_PUBLIC_URL`) reachable by GitHub; confirm
  the repo's *Settings → Webhooks* shows AP's endpoint with recent green deliveries.
- **Why not direct?** GitHub *could* go direct (its webhooks are simple), but it still needs a public
  URL, and AP already gives us the trigger + token store for free. Integrations stay on AP.
