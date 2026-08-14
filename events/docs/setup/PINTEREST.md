# Pinterest setup (Activepieces piece — polling)

Pinterest is an **Activepieces** trigger: AP holds the OAuth connection and polls Pinterest; CUGA
arms a flow that calls back `/invoke` on each new item. The agent holds **no** Pinterest credential.

```
Pinterest change ─▶ AP (pinterest piece, poll) ─▶ /invoke (agent) ─▶ [delivery]
```

## What you'll need
- A Pinterest account.
- Activepieces reachable, with a **Pinterest OAuth connection**.

## Steps

1. **Connect Pinterest in Activepieces** — AP → **Connections** → **Pinterest** → authorize.

2. **Arm a watcher**:
   ```
   /push when there's a new pin on my pinterest board, share it
   /push when I get a new pinterest follower, thank them
   ```

## The triggers

| Trigger (what you say) | AP trigger | Slot |
|---|---|---|
| `new_pin` — *"when there's a new pin on my board…"* | `newPinOnBoard` (poll) | `board` (board id, required) |
| `new_board` — *"when a new board is created on pinterest…"* | `newBoard` (poll) | — |
| `new_follower` — *"when I get a new pinterest follower…"* | `newFollower` (poll) | — |

`new_pin` needs a `board_id` (a required AP dropdown); name the board id in your utterance and CUGA
passes it through, otherwise the arm asks. `new_board` and `new_follower` need no slot.

## Verify
Arm one, then create a board / add a pin / gain a follower. Polling triggers fire on the next tick;
the agent's summary is delivered to the origin channel.

## Troubleshooting
- **"connect your Pinterest"** — no AP connection yet; do step 1.
- **`new_pin` won't publish** — the board dropdown couldn't resolve; confirm the board id is on the
  connected account.
