# Palette × CUGA — Integration Proposal

**Internal proposal for the CUGA team · 2026-07-06**

> **The ask:** Make Palette a **sub-agent** inside CUGA. A user asks for a deck in plain English, CUGA delegates the whole job to Palette and hands back a `.pptx`. Nobody ever types the word "Palette."

---

## 1. What we're plugging in

Palette turns a topic into an editable deck.

**What it does**
- Topic or reference docs → **editable `.pptx`** + PNG previews.
- 3 stages: plan → design+code → geometry repair.
- Multi-turn: draft · build · retry a slide · natural-language edit.

**What it is under the hood**
- Fine-tuned LoRA (served via RITS) + a render toolchain.
- Node/pptxgenjs · LibreOffice · Poppler · IBM Plex fonts.
- **Already deployed on a machine** — a running HTTP service.

It's not a function — it's a small autonomous app with its own model. That fact decides how it should plug in.

---

## 2. The decision — three ways into CUGA

All three are **auto-selected by description**; the user never names them. The only question is which shape matches what Palette actually is.

- **Skill** — a `SKILL.md` playbook that CUGA's own LLM reads and executes in a sandbox.
- **Tool** — an OpenAPI / MCP endpoint the supervisor calls directly.
- **Sub-agent** — a named specialist the supervisor delegates whole tasks to.

---

## 3. Skills vs Tools vs Sub-agents — pros & cons for Palette

### Skill — ❌ wrong fit
*A SKILL.md the base LLM reads & runs in a sandbox.*

| Pros | Cons |
|---|---|
| Zero infra; auto-loaded on match | Palette's brain is a fine-tuned model, not prose — can't live in a prompt |
| Good for procedures/playbooks | Sandbox would need Node + LibreOffice + fonts + RITS access |
| | If it just calls the service, it's redundant with a tool |

### Tool (OpenAPI / MCP) — 🟠 OK for v1
*An endpoint the supervisor calls directly.*

| Pros | Cons |
|---|---|
| Near-zero effort — Palette already exposes `/openapi.json` (it's FastAPI) | Deck-building is multi-turn; loose tool calls are awkward |
| Auto-invoked by description | Builds run minutes — clashes with the one-call/one-answer model |

### Sub-agent — ✅ best fit
*A named specialist the supervisor delegates to (internal `CugaAgent`, or external over A2A).*

| Pros | Cons |
|---|---|
| Matches Palette: autonomous, multi-turn, own model | One extra config layer |
| Clean "hand off the whole deck job" delegation | Needs a sharp description to route reliably |
| Wraps the tool — keeps Palette in its own deployment | (external A2A only) needs a thin adapter |

---

## 4. Recommendation

**Make Palette a sub-agent — backed by its HTTP API.**

An **internal supervisor agent** (`deck_designer`) that owns exactly one tool: Palette's already-deployed OpenAPI service.

You get sub-agent delegation **and** near-zero wiring — no A2A adapter to build. Palette already runs on its machine; CUGA just points at it.

- **Now:** internal sub-agent + OpenAPI tool.
- **Later:** flip to external **A2A** if Palette should live in its own process / speak the protocol. The supervisor-side contract doesn't change.

---

## 5. On-need, without the user naming it

This is CUGA's **default** routing — the same way built-in tools fire.

> *"put together a deck on our Q3 results for the board"*

1. Supervisor matches the request against registered **descriptions**.
2. Sees `deck_designer` — "generates presentations / decks / slides."
3. Delegates the whole task to it. The word "Palette" never appears.
4. `deck_designer` drives Palette (draft → build), returns the `.pptx`.

**"Built-in"** = ship it in the *default* config → present every session, zero user setup.

Reliability lives entirely in the description: clear **triggers** (deck · slides · presentation · pitch · readout) and **anti-triggers** (don't fire for a plain text summary).

---

## 6. How it wires — concretely

Two entries in the default config:

```yaml
# mcp_servers.yaml — Palette as a tool (it's already deployed)
services:
  - palette:
      url: http://<palette-host>:18814/openapi.json
      description: "Generates editable .pptx decks from a topic or docs."
```

```yaml
# supervisor.yaml — an internal sub-agent that owns only that tool
agents:
  - name: deck_designer
    description: >
      Builds & iterates on PowerPoint decks / slides / presentations.
      Use whenever a user wants a deck — even if they don't say Palette.
    apps: [ palette ]
```

One thing to add on the **Palette side**: a synchronous `POST /generate` facade (draft→build in one call) so the supervisor sees one clean call instead of build-then-poll.

---

## 7. What we need from the CUGA team

**Decision**
- Agree: Palette lands as a **built-in sub-agent**, not a skill.
- Ship it in the **default** config so it's on for everyone.

**Small asks**
- A stable URL + auth for the deployed Palette.
- Review the `deck_designer` description & trigger phrasing.
- Add a `/generate` facade (our side) for the long build.

**Bottom line:** working v1 in an afternoon — no changes to CUGA's engine, no A2A adapter, Palette stays where it's already deployed.
