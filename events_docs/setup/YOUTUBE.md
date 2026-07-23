# YouTube setup (Activepieces piece — polling, RSS-backed)

YouTube is an **Activepieces** trigger: AP polls a channel's video feed and CUGA arms a flow that
calls back `/invoke` on each new upload. It watches a **public** channel feed — no OAuth needed for
the `new-video` trigger, just the channel identifier.

```
New upload ─▶ AP (youtube piece, poll) ─▶ /invoke (agent) ─▶ [delivery]
```

## What you'll need
- The channel you want to watch, as a **channel id, URL, or `@handle`**.
- Activepieces reachable. (The `new-video` feed trigger needs no user OAuth connection.)

## Steps

1. **Arm a watcher** — name the channel; CUGA fills the `channel_identifier`:
   ```
   /push when my youtube channel posts a new video, share it
   /push watch youtube channel @Fireship for new videos and summarize them
   ```
   If you don't name a channel, the concierge asks (*"Which YouTube channel (id, URL, or @handle)?"*).

## The trigger

| Trigger (what you say) | AP trigger | Slot |
|---|---|---|
| `new_video` — *"when my youtube channel posts a new video…"* | `new-video` (poll) | `yt_channel` (id / URL / @handle, required) |

## Verify
Arm it, then wait for the channel to publish (or watch a high-frequency channel). The poll fires on
the next tick with the video title + link; the agent's summary is delivered to the origin channel.

## Troubleshooting
- **Nothing fires** — the `channel_identifier` didn't resolve to a feed; try the raw channel id
  (`UC…`) rather than a handle.
- **Old videos re-fire on first run** — the poll baselines on arm; only uploads after arming fire.
