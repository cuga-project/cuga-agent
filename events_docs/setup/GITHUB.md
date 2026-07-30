# GitHub setup (Activepieces backend)

GitHub is an **integration**, so it runs on **Activepieces**: AP watches your repo (a `new_pr`
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
exactly like an under-scoped token. `POST /api/events/connect/github/token` **rejects** a pasted PAT
with a `400` for exactly this reason — GitHub is registered as an OAuth connector
(`oauth.py`: `kind:"oauth"`), so the endpoint refuses to build a SECRET_TEXT connection AP's piece
could never use. Connect via OAuth.

## Two DIFFERENT GitHub credentials — don't conflate them

1. **The OAuth App connection** (this page) — what Activepieces uses to watch the repo. Human
   browser consent; lives encrypted in AP.
2. **`GITHUB_TOKEN` in `.env`** — a **fine-grained PAT** the TEST HARNESSES use to act on the
   pinned test repo (create the probe branch/PR, strip webhooks). It needs, on that repo:
   **Contents: Read and write** · **Pull requests: Read and write** · **Webhooks: Read and write**.
   ⚠ When editing a fine-grained token's permissions, re-check ALL three before saving — an edit
   that adds one can silently drop another (bit us live: adding Pull requests dropped Contents →
   branch creation 403'd). Editing permissions keeps the same token string; no `.env` change needed.

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

## Rotating credentials

Remember the [two different credentials](#two-different-github-credentials--dont-conflate-them): they
rotate differently.

**The AP watch connection (OAuth).** To rotate what Activepieces watches the repo with, just
**re-consent**: open `<EVENTS_PUBLIC_URL>/api/events/connect/github` again (or Studio → Integrations →
GitHub → Connect). The fresh `OAUTH2` connection overwrites the old one and AP owns the refresh
lifecycle from there — there is no token to paste. If a flow was already armed against a dead
connection it fails at run time with `401 Bad credentials`; the failing step's error shows in the run
log (`GET /api/events/runs/<id>`), not in the response to the connect call.

**The `.env` `GITHUB_TOKEN` (test harnesses only).** This fine-grained PAT is used **only** by the live
test harnesses — nothing arms AP from it. When it expires or is revoked, edit it in `.env` and
`make reload`. Its permissions (**Contents · Pull requests · Webhooks**, all R/W on the pinned repo)
are what the branch/PR/webhook probes need; a scope drop surfaces as `403 Resource not accessible` from
the harness, never as a `CONNECT NEEDED` in the app.

## Troubleshooting
- **`CONNECT NEEDED — connect your github`** — GitHub is an OAuth connection, so this message has
  **two** real causes (the `.env` PAT is not one of them — it never lands in AP):
  1. **You haven't consented yet** — run step 3 (open `/api/events/connect/github` and approve).
  2. **Activepieces is down or unreachable.** The gate calls AP to ask whether the connection exists,
     and on *any* exception it assumes "not connected". A stopped AP container therefore produces an
     identical "connect your credentials" message. Check first:
     `podman ps` and `curl -s localhost:8081/api/v1/flags`.
- **Slack asks you to connect even though the Studio says connected** — GitHub connections are keyed
  per `(tenant, user)` as `ea::<tenant>::<user>::github`. A Slack sender whose Slack id has never been
  account-linked falls back to the operator principal (`local`), so it looks up a *different* key than
  the one the Studio's web session consented under. Link the account first (`/link <token>`), then
  consent as that user.
- **Webhook never fires** — AP needs a public URL (`EVENTS_PUBLIC_URL`) reachable by GitHub; confirm
  the repo's *Settings → Webhooks* shows AP's endpoint with recent green deliveries.
- **Why not direct?** GitHub *could* go direct (its webhooks are simple), but it still needs a public
  URL, and AP already gives us the trigger + token store for free. Integrations stay on AP.
