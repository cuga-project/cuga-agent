# ngrok setup (the stable public URL — do this before any webhook connector)

Some connectors receive events **over the internet** (Telegram and Slack webhooks, Gmail/GitHub OAuth
callbacks), so CUGA needs a **public HTTPS URL**. Out of the box `events_up.sh` gives you a free
cloudflared **quick tunnel** — but its URL is random and **flaps mid-session**, which silently breaks
Slack and OAuth. ngrok fixes that: a **free reserved domain** that never changes, so you configure
Slack/Gmail **once, forever**.

```
internet ─▶ https://<you>.ngrok-free.app ─▶ ngrok agent ─▶ localhost:8100 (CUGA)
```

> **This is a one-time setup and it is strongly recommended.** Skip it only for a throwaway local demo
> that touches no webhook/OAuth connector (Discord + Box-direct need no public URL at all). For the
> full mechanics of how the URL is wired, see [../PUBLIC_URL.md](../PUBLIC_URL.md).

## What you'll need
- A free ngrok account — no domain to buy, no credit card.
- `brew install ngrok` (or your platform's installer — see below).

## Steps

1. **Install ngrok**
   ```bash
   brew install ngrok            # macOS
   #  Linux: see https://ngrok.com/download   ·   or `sudo snap install ngrok`
   ```

2. **Create the account + verify your email.** Sign up at
   <https://dashboard.ngrok.com/signup>, then **verify your email** —
   ngrok blocks the agent from starting until you do (`ERR_NGROK_...`), so don't skip it.

3. **Add your authtoken (once per machine).** Copy it from
   <https://dashboard.ngrok.com/get-started/your-authtoken>, then:
   ```bash
   ngrok config add-authtoken <YOUR_AUTHTOKEN>
   ```
   This writes `~/.config/ngrok/ngrok.yml`; you never pass the token again.

4. **Reserve your free static domain.** Go to
   <https://dashboard.ngrok.com/domains> → **New Domain**. The free tier gives you **one** domain,
   shaped like `your-name.ngrok-free.app`. Copy the exact string.

5. **Point CUGA at it** — in `.env`:
   ```
   EVENTS_NGROK_DOMAIN=your-name.ngrok-free.app
   ```
   That single line is all the wiring: `events_up.sh` now starts **ngrok** (not cloudflared) on
   `:8100`, serves it on that domain, and pins `EVENTS_PUBLIC_URL=https://your-name.ngrok-free.app`.
   Bring the server up (`make up`) — or `make restart` if it was already running (the tunnel domain is
   read only when the tunnel starts, so `make reload` won't pick it up).

## Verify
```bash
make public-url          # prints https://your-name.ngrok-free.app + the exact Slack/Gmail strings
curl -s https://your-name.ngrok-free.app/api/events/status   # → 200 from anywhere on the internet
make tunnels             # CUGA tunnel: ngrok STATIC — agent up, url stable
```
Then set the **Slack Request URL** and **Gmail redirect URI** to that domain **once** (the connector
guides give the exact paths). Because the URL never changes, you never touch those consoles again.

## What ngrok covers (and what it doesn't)
The free tier is **one domain**, which we spend on the **CUGA** server — that's what Slack's Request
URL and every OAuth callback point at. The separate **AP tunnel** (Activepieces `:8081`, which
Telegram's webhook uses) stays on a cloudflared quick tunnel, so after an **AP** restart just re-run
`make channels` to re-arm Telegram. If you want *both* stable, reserve a second ngrok domain (or a paid
plan) and pass it to AP via `AP_TUNNEL_URL`, or use a Cloudflare named tunnel — see
[../PUBLIC_URL.md](../PUBLIC_URL.md).

## Troubleshooting
- **`ERR_NGROK_105` / `ERR_NGROK_107` (auth)** — authtoken missing or wrong. Re-run
  `ngrok config add-authtoken <token>` (step 3).
- **`ERR_NGROK_...` about the domain / "not authorized for domain"** — the `EVENTS_NGROK_DOMAIN` value
  doesn't match a domain you reserved, or your email isn't verified. Re-check
  [dashboard.ngrok.com/domains](https://dashboard.ngrok.com/domains) and copy the string verbatim.
  `events_up.sh` surfaces the exact `ERR_NGROK_*` code from `cuga_tunnel.log` on a failed start.
- **`MISSING: ngrok`** on `make up` — `EVENTS_NGROK_DOMAIN` is set but ngrok isn't installed.
  `brew install ngrok`.
- **URL 200s but Slack/OAuth still fail** — the console still points at an old cloudflared URL. Run
  `make public-url` and paste the current (now stable) domain into Slack/Gmail one last time.
- **The domain didn't take effect after editing `.env`** — the tunnel domain is read at tunnel start.
  Use **`make restart`**, not `make reload` (which deliberately keeps the running tunnel).
