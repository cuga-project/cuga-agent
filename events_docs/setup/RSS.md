# RSS / Atom setup (Activepieces piece — polling, no OAuth)

RSS is an **Activepieces** trigger that polls any **public feed URL** and fires `/invoke` on each new
item. No credentials — just the feed URL.

```
New feed item ─▶ AP (rss piece, poll) ─▶ /invoke (agent) ─▶ [delivery]
```

## What you'll need
- The **feed URL** you want to watch (any public RSS/Atom feed).
- Activepieces reachable. No OAuth connection.

## Steps

1. **Arm a watcher** — name the feed URL:
   ```
   /push when a new item appears in https://blog.example.com/rss, summarize it in slack
   ```
   If you don't include a URL, the concierge asks (*"Which RSS/Atom feed URL should I watch?"*).

## The trigger

| Trigger (what you say) | AP trigger | Slot |
|---|---|---|
| `new_item` — *"when a new item appears in <feed>…"* | `new-item` (poll) | `rss_feed_url` (required) |

## Verify
Arm it against an active feed, then wait for the feed to publish (or use a high-frequency feed). Or
test instantly with a synthetic fire (no waiting, no live flow):
```bash
curl -s -X POST localhost:8100/api/events/synth-fire \
  -H "x-gateway-token: $GATEWAY_TOKEN" -H "content-type: application/json" \
  -d '{"source":"rss","prompt":"Summarize this feed item."}'
```

## Troubleshooting
- **Nothing fires** — the feed URL didn't resolve; open it in a browser to confirm it's a real feed.
- **Old items re-fire on first run** — the poll baselines on arm; only items after arming fire.
