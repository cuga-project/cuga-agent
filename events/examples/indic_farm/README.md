# Indic Farm Assistant — a voice-first WhatsApp agent

The Farm Assistant team's app ([cyril-shaji/CUGA-Voice-RAG-Agent](https://github.ibm.com/cyril-shaji/CUGA-Voice-RAG-Agent))
rebuilt on the roster + the eventing/channel layers. A farmer sends a **voice note** on WhatsApp in any major
Indian language; the answer comes back grounded in a farming handbook, in the same language, as text with page
citations and then as a voice reply.

**It keeps their RAG shape, and the whole flow is four lines of env.** The handbook is searched on EVERY
question before CUGA is called, and the passages travel in the message — the model cannot skip the search or
reword it. The agent has no tools; its job is to answer from the context it was handed.

```
EVENTS_SPEECH_URL=http://localhost:8300       # audio → text, and text → audio
EVENTS_KNOWLEDGE_URL=http://localhost:8770    # the handbook
```

That is the entire configuration of the eventing layer. Which model hears a voice note, which voice answers,
what language it is and what to strip before speaking are decided inside the speech service — so swapping
Whisper for Sarvam, or adding Tamil, never touches the channel, the agent, or this repo's events code.

Architecture, diagrams and measurements: `~/explorations/indic_usecase/`.

| File | What it is |
|---|---|
| (no pipeline config) | the events layer calls two services; an unset URL means that stage is off |
| `handbook/` | **their code**: `ingest.py` + `cuga_compat.py` unchanged, and `handbook.py` (their search + "[Page N]" formatting), with optional HTTP and MCP frontends |
| `mcp_servers.yaml` | only for the optional TOOL variant — the example does not use it |
| `e2e_voice.py` | end-to-end proof with a fake Meta Graph API — no real WhatsApp traffic |
| `../rosters/indic_farm_assistant.yaml` | the roster: supervisor + `farm_assistant` (their prompt) |
| `../speech_service/` | the reference speech service (whisper · piper · parler · mms) |

## Run it locally

Five processes. Ports: speech 8300 (+8301 for a second voice engine), handbook MCP 8765, CUGA 7860, events 8100.

**1 · Build the handbook index** (once; needs the PDF from their repo)

```bash
cd events/examples/indic_farm/handbook
cp .env.example .env                 # the embedding model must match at ingest AND at search time
python ingest.py /path/to/farmerbook.pdf
```

**2 · Speech service** — the Linux container runs Whisper + Piper:

```bash
cd events/examples/speech_service
podman build --platform linux/arm64 -t speech-service:dev -f Containerfile .
podman run --rm -p 8300:8300 -v ~/.cache/huggingface:/root/.cache/huggingface speech-service:dev
```

Piper has no Tamil voice, so point those languages at another engine — inside the service, where that
decision belongs:

```bash
SPEECH_TTS=piper SPEECH_TTS_BY_LANGUAGE='{"ta-IN": "parler"}' uvicorn app:app --port 8300
```

**3 · The handbook service**

```bash
cd events/examples/indic_farm/handbook && python handbook_service.py    # → http://127.0.0.1:8770
```

It holds the index (and its exclusive interprocess lock) in its own process, so `ingest.py` and the events
service never fight over the store.

**4 · CUGA, as the farm supervisor**

```bash
CUGA_EVENTS_ENABLED=true \
CUGA_DBS_DIR=/tmp/farm-dbs \
CUGA_SUPERVISOR_ROSTER=events/examples/rosters/indic_farm_assistant.yaml \
  .venv/bin/cuga start demo
```

(No `MCP_SERVERS_FILE`: the agent has no tools.)

> **`CUGA_DBS_DIR` is not optional if you also run the tests.** A roster is SEEDED into the config store
> (`src/cuga/dbs/cuga.db`) on boot, and `tests/events` reads that same store — so after running this stack
> without an override, `/run` takes the supervisor path in tests and `test_split_service.py` fails. If that
> already happened:
> `sqlite3 src/cuga/dbs/cuga.db "delete from agent_configs where agent_id in ('farm_assistant','cuga')"`.

**5 · The events service**

```bash
EVENTS_SPEECH_URL=http://localhost:8300 \
EVENTS_KNOWLEDGE_URL=http://localhost:8770 \
  .venv/bin/python -m cuga.backend.events.service
```

For a **real** WhatsApp number, set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`,
`WHATSAPP_VERIFY_TOKEN` and point Meta's webhook at `<public-url>/api/events/whatsapp/events`
(see `events/docs/setup/`). For a **local test**, don't: use the harness below.

## Prove it without touching WhatsApp

`e2e_voice.py` starts a fake Meta Graph API, signs the webhook the way Meta signs it, and captures the replies.
Run the events service with `WHATSAPP_GRAPH_BASE=http://127.0.0.1:8399`, fake credentials, and
`LOCAL_HONOR_TELEGRAM/DISCORD/SLACK=false` so no real channel is touched:

```bash
python e2e_voice.py --audio question_hi.ogg      # a voice note (ogg/opus, as WhatsApp sends)
python e2e_voice.py --text "बीज की गुणवत्ता की पहचान कैसे करें?"
```

Make a test voice note with any TTS, e.g. `say -v Lekha -o q.aiff "…" && ffmpeg -i q.aiff -ac 1 -c:a libopus q.ogg`.

Measured on a laptop (watsonx `gpt-oss-120b`), Hindi voice note, webhook acked in 0.07 s:

| passages (`limit`) | transcribe | retrieve | text reply | voice reply |
|---|---|---|---|---|
| 5 (theirs), first call | 7.0 s | **14.8 s** (loads the embedding model) | 84 s | 90 s |
| 5, warm | 6.6 s | 0.5 s | 47 s | 53 s |
| **3, warm** | 6.5 s | 0.5 s | **22 s** | 27 s |

Context size dominates, because the passages pass through the supervisor and then the specialist — both read
them. `limit: 3` roughly halves the wait; a single-agent deployment (no supervisor) would save the other read.
Tamil's voice reply takes ~3 minutes on Parler, which is why `flush` sends the text first.

## Changing things

- **Different speech vendor**: point `EVENTS_SPEECH_URL` at a service that speaks the two endpoints — a
  Sarvam-backed one, a GPU box, a partner team's. The events layer never knew which model it was talking to.
- **A language Whisper mishears** (Telugu, unhinted): `SPEECH_STT_LANGUAGE=te-IN` on the speech service.
- **No voice at all**: unset `EVENTS_SPEECH_URL`. Voice notes are then left alone and typed chat is unaffected.
- **More tools** (weather, mandi prices): add the server to `mcp_servers.yaml` and name it under the agent in the
  roster.
