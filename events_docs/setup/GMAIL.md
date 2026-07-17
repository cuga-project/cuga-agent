# Gmail setup (Activepieces backend)

Gmail is an **integration**, so it runs on **Activepieces** — and it's the one where AP genuinely
earns its keep: AP does the **OAuth consent + token refresh**, so you never touch a refresh token.
Gmail is both a **PUSH source** and an **email delivery sink** (`send_email`).

```
new email  ─▶ AP gmail trigger (OAuth) ─▶ /invoke (mailbot) ─▶ deliver
brief/verdict ─▶ /invoke ─▶ AP gmail send_email ─▶ your inbox     (email as a sink)
```

## Triggers & permissions

**One connection covers all four triggers** — the connect flow requests the single scope
`https://www.googleapis.com/auth/gmail.modify` (read + labels + send), so there is nothing extra to
grant per trigger:

| Trigger (what you say) | Watches |
|---|---|
| `new_email` — *"when a new email arrives, summarize it"* | the inbox |
| `new_labeled_email` — *"when I label an email 'Read-later'…"* (needs the **label** name) | one label |
| `new_attachment` — *"when an email arrives with a resume attached…"* | attachments |
| `new_gmail_label` — *"when a new gmail label is created…"* | the label list |

All four are **polling** triggers: Activepieces checks on its own schedule and cannot be fired by
machine — arming is verifiable automatically; a real fire needs a real email (see TESTING.md).

Seeded agents: **`mailbot`** (summarize/triage your Gmail) and **`resume_judge`** (Box → judge →
email). Both are **per-user** — each employee logs into *their own* Gmail.

## What you'll need
- A **Google Cloud OAuth 2.0 client** (client id + secret) with the Gmail scope.
- A public HTTPS URL (`EVENTS_PUBLIC_URL`) for the OAuth redirect + the AP webhook.

## Steps
1. **Create an OAuth client** — Google Cloud Console → *APIs & Services → Credentials → Create OAuth
   client ID* (Web application). Enable the **Gmail API**. Add the redirect URI:
   `<EVENTS_PUBLIC_URL>/api/events/connect/gmail/callback`.

2. **Add the client creds** to `.env` (or the Studio → Admin → OAuth-apps panel):
   ```
   EVENTS_OAUTH_GMAIL_CLIENT_ID=…apps.googleusercontent.com
   EVENTS_OAUTH_GMAIL_CLIENT_SECRET=…
   ```
   Restart the server.

3. **Each user logs in** (consent — a pasted token will NOT work; AP does the code exchange + refresh):
   ```
   open  GET /api/events/connect/gmail        # → Google consent → callback → AP connection
   ```

4. **Arm a watcher / use email delivery** — ask the concierge:
   *"when an email from my boss arrives, summarize it and message me"* (PUSH), or
   *"every day at 8am email me a market brief"* (email as a delivery sink).

## Verify
```bash
# full integration e2e (NOW/CRON/POLL + PUSH box/github/gmail):
GATEWAY_TOKEN=<from .env> EVENTS_SERVER_URL=http://localhost:7860 \
  .venv/bin/python tests/events/live_integrations_e2e.py
```
With Gmail connected, the `PUSH · gmail` leg **arms a real AP flow** (an `ap_flow_id` appears). Then
send yourself a matching email → a summary is delivered.

## Troubleshooting
- **`gmail isn't configured for OAuth on this deployment`** — `EVENTS_OAUTH_GMAIL_CLIENT_ID/_SECRET`
  aren't set. Add them (step 2) and restart.
- **`CONNECT NEEDED — connect your gmail`** — creds are set but this user hasn't logged in; open
  `GET /api/events/connect/gmail`.
- **`redirect_uri_mismatch`** — the redirect URI in Google Cloud must exactly match
  `<EVENTS_PUBLIC_URL>/api/events/connect/gmail/callback` (https, no trailing slash).
- **Token expired** — AP refreshes it, so this shouldn't happen *while the OAuth app is published*.
  But in Google's **Testing** publishing mode the refresh token is invalidated after **7 days**, and
  no amount of refreshing survives that. Symptom: Gmail worked all week, then every run says
  `CONNECT NEEDED`. Fix: re-Connect in Studio → Integrations, or move the OAuth app to **In
  production** in Google Cloud → OAuth consent screen to stop the 7-day clock.
- **`CONNECT NEEDED` but you're sure you connected** — the connect gate asks *Activepieces* whether
  the connection exists, and it reports "not connected" when AP is simply **unreachable**. Check AP
  is up (`curl -s localhost:8081/api/v1/flags`) before re-authorizing anything.
