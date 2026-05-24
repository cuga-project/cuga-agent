# Knowledge Engine Demo — HR Benefits Assistant

This example demonstrates CUGA's **knowledge engine** with two distinct
document scopes — **agent-level** (persistent, shared across conversations) and
**session-level** (ephemeral, scoped to one chat) — using a realistic HR
Benefits Assistant corpus.

## What this demonstrates

- **Agent-level vs session-level knowledge.** Company policies (handbook, health
  plan, PTO, 401(k)) live at agent scope and persist across sessions. A specific
  employee's personal documents (pay stub, benefits enrollment, PTO balance) live
  at session scope and never leak outside the conversation.
- **Cross-document RAG reasoning** over a corpus that has real interaction
  between documents — e.g. the 2026 HSA IRS family limit from the health plan
  doc + the employer contribution rule + Sarah's YTD payroll deductions all
  need to be combined to answer "am I on track to max out my HSA?"
- **Two integration paths:** the **SDK** (this directory's `main.py`) and the
  full **UI** (`cuga start demo_knowledge` + manage page + chat composer).

## Narrative

You're running an HR Benefits Assistant. Every employee who talks to the agent
sees the same company policies — those are uploaded once at agent scope and
published with the agent config. When an individual employee starts a
conversation, they attach their own personal documents (their current pay stub,
their benefits elections, their PTO balance) at session scope. The agent must
reason across both: "according to the company plan, and given your numbers,
here is what you should do." That cross-scope reasoning is the whole point.

We use a fictional company "Acme Corp" and a fictional employee "Sarah Chen".
All numbers (deductibles, premiums, accrual rates, IRS limits) are realistic
for 2026 so the agent's answers are checkable.

## File layout

```text
docs/examples/knowledge_demo/
├── README.md
├── pyproject.toml
├── .python-version
├── .env.example
├── main.py                                       # SDK walkthrough (Path A)
└── sample_data/
    ├── agent_level/                              # upload once, persist across sessions
    │   ├── 01_employee_handbook.md
    │   ├── 02_health_insurance_plan.md
    │   ├── 03_pto_policy.md
    │   └── 04_retirement_401k_plan.md
    └── session_level/                            # attach per-conversation
        ├── sarah_chen_benefits_enrollment.pdf
        ├── sarah_chen_pto_balance.pdf
        └── sarah_chen_paystub_march_2026.pdf
```

> The two scopes intentionally use different formats: company policies are
> markdown (how HR authors them), while Sarah's personal documents are PDFs
> (how an employee actually receives a pay stub or enrollment confirmation).
> Both flow through the same `agent.knowledge.ingest(...)` call — Docling
> handles PDF/DOCX/PPTX/XLSX/HTML/MD/images uniformly under the hood.

## Prerequisites

- Python 3.12 (see `.python-version`)
- A working LLM provider key — follow the
  [main README LLM configuration section](../../../README.md#llm-configuration---advanced-options)
- Repository installed once from the repo root:

  ```bash
  cd cuga-agent
  uv sync
  ```

Per
[`CONTRIBUTING.md`](../../../CONTRIBUTING.md), this example does not carry its
own `uv.lock`; it resolves `cuga` from a path dependency and shares the
root lockfile's resolution.

---

## Path A — Programmatic SDK walkthrough

`main.py` uses the `agent.knowledge.*` SDK surface directly, with no server.

### 1. Configure LLM access

```bash
cd docs/examples/knowledge_demo
cp .env.example .env
# Edit .env: uncomment the block for your LLM provider and add your key.
# Leave the three DYNACONF_KNOWLEDGE__* lines at the top enabled —
# they override the repo default (`enabled = false`) for this demo.
```

### 2. Run the demo

```bash
uv run --project ../../../ main.py
```

Add `--reset` on any subsequent run to wipe previously ingested agent docs
first — the script re-ingests from scratch:

```bash
uv run --project ../../../ main.py --reset
```

### 3. Expected console output (abridged)

```text
Building CugaAgent with enable_knowledge=True ...
=== 1. Ingest agent-level documents ===
Ingesting [agent] 01_employee_handbook.md ...
  → 01_employee_handbook.md: completed
... (4 docs total)
=== 2. Agent-level RAG via agent.invoke() ===
Q: What's the PTO carryover limit if I don't use all my days before year-end?
A: Acme Corp allows you to carry over up to 40 hours (equivalent to 5 days) of
   unused PTO into the next calendar year. Any PTO balance above the 40-hour
   limit is forfeited on December 31.
=== 3. Direct search API (no LLM) ===
Top agent-level hits for 'HSA contribution limit family coverage':
  • 02_health_insurance_plan.md  score=0.615  preview='Acme Corp Health Insurance Plan Summary Health Savings Account (HSA) Employees enrolled in Option C (HDHP) are eligible '
=== 4. Ingest session-level documents ===
Session thread_id: sarah-demo-session
  → sarah_chen_benefits_enrollment.pdf: completed
... (3 docs total)
=== 5. Session-scoped search API ===
Top session-level hits for 'current PTO balance and usage this year':
  • sarah_chen_pto_balance.pdf  score=0.636  preview='PTO Balance Statement Current Balance Summary PTO accrued year-to-date (Jan–Apr), Hours = 53.33 ...'
=== Done. For end-to-end session RAG … run `cuga start demo_knowledge` …
```

> Exact similarity scores may vary slightly by embedding-model version; the
> phrase "40 hours (5 days)" and top-result filenames are the meaningful check.

### What `main.py` exercises

| Step | SDK call | Verified against |
| --- | --- | --- |
| Build agent | `CugaAgent(enable_knowledge=True)` | `src/cuga/sdk.py` |
| Reset (optional) | `agent.knowledge.list_documents` + `delete_document` | `src/cuga/backend/knowledge/client.py` |
| Ingest | `agent.knowledge.ingest(path, scope, thread_id)` | same |
| RAG query | `agent.invoke(question)` | `src/cuga/sdk.py` |
| Direct search | `agent.knowledge.search(query, scope, thread_id)` | `src/cuga/backend/knowledge/client.py` |
| Cleanup | `await agent.aclose()` | `src/cuga/sdk.py` |

### A note on session scope in the SDK

The SDK auto-injects knowledge LangChain tools at agent init with no
`thread_id`, so **session scope is only exercised via the direct client API**
(`agent.knowledge.ingest/search(scope="session", thread_id=...)`) in Path A.
For the agent itself to reason over session docs via `agent.invoke(...)`,
run Path B below — the server wires `thread_id` through the MCP tools.

---

## Path B — Full UI walkthrough (`cuga start demo_knowledge`)

This is the end-to-end demo where the agent reasons across **both** scopes.

### 1. Start the knowledge-enabled demo

```bash
cuga start demo_knowledge --reset
```

Wait for the Knowledge subsystem status to reach **Connected**.

### 2. Upload agent-level documents and publish

1. Open http://localhost:7860/manage
2. Scroll to the **Knowledge** section → click **Configure knowledge base**
3. On the **Documents** tab, drag-and-drop (or upload) all four files from
   `sample_data/agent_level/`
4. Wait for each document to reach **completed** status
5. (Optional) rename the agent to "HR Benefits Assistant" in the Agent Name field
6. Click **Publish** at the bottom of the manage page

### 3. Attach session-level documents in chat

1. Open http://localhost:7860/chat
2. In the composer, click the attach icon and upload all three files from
   `sample_data/session_level/`
3. The three docs appear in the Knowledge side panel tagged as session-scoped

### 4. Run the demo prompts

#### Prompt 1 — agent-level only

> What's the vacation carryover limit if I don't use all my PTO before year-end?

**Expected:** Agent searches agent-level docs, finds `03_pto_policy.md`, answers
that up to **40 hours (5 days)** may carry over; anything above is forfeited on
December 31 unless a carryover waiver is granted.

#### Prompt 2 — session-level only

> How many PTO days do I have left right now, and what have I used this year?

**Expected:** Agent searches session-level docs, finds `sarah_chen_pto_balance.pdf`,
reports **61.33 hours (7.67 days)** remaining and the usage history: ski trip in
January, a personal day in February, flu sick days in March — 32 hours used YTD.

#### Prompt 3 — combined (the money query)

> Based on my pay stub and HSA enrollment, am I on track to max out my HSA
> contributions for 2026? How much more should I contribute per paycheck to
> hit the limit exactly?

**Expected:** Agent combines multiple documents:

- `02_health_insurance_plan.md` (agent): 2026 family HSA IRS limit = **$8,550**;
  Acme employer contribution for family coverage = **$2,000**/year
- `sarah_chen_benefits_enrollment.pdf` (session): family coverage, $250/paycheck
  employee contribution
- `sarah_chen_paystub_march_2026.pdf` (session): YTD employee HSA contribution = $1,770

Math: employee room = $8,550 − $2,000 = **$6,550**. Current election = $250 ×
26 paychecks = **$6,500** → Sarah is **$50 short** of the max. Recommendation:
bump contribution by ~$2/paycheck or make a one-time $50 catch-up by 12/31.

#### Prompt 4 — combined (planning)

> I'm planning a 10-day vacation in July. Given my PTO balance, my accrual
> rate, and company policy — will I have enough time? Is my request compliant
> with the advance-notice rule?

**Expected:** Agent pulls the 2-week advance-notice rule for 3+ day requests
from `03_pto_policy.md`, then combines with Sarah's balance (61.33h) and
accrual rate (13.33h/month) from `sarah_chen_pto_balance.pdf`:

- By July 6, Sarah will have accrued ~40 additional hours → ~100 hours total
- A 10-day vacation = 80 hours → **yes**, she has enough
- April 10 request for July 6 vacation is well beyond the 2-week window → **compliant**

### 5. Stop

```bash
cuga stop demo_knowledge
```

---

## How it works

- **Agent-level collection:** `kb_agent_{sanitized_agent_id}_{vector_config_hash}`,
  persisted in `<cwd>/.cuga/knowledge/` across every conversation
- **Session-level collection:** `kb_sess_{sanitized_thread_id}`, ephemeral — one
  per conversation; reaped by background cleanup after ~7 days
- **Ingestion:** Docling parses the input (Markdown / PDF / DOCX / HTML / images) →
  chunker splits the text → embeddings (fastembed by default) are written to the
  vector store (SQLiteVec by default) + metadata into `metadata.db`
- **Retrieval:** agent's reasoning graph calls knowledge search tools that run
  against the collection resolved from the caller's agent / session identity

For the full architecture — startup flow, backends, security model — see
[`src/cuga/backend/knowledge/KNOWLEDGE_PIPELINE.md`](../../../src/cuga/backend/knowledge/KNOWLEDGE_PIPELINE.md).

## Further reading

- Main README — [Knowledge Base section](../../../README.md#knowledge-base)
- [Knowledge pipeline reference](../../../src/cuga/backend/knowledge/KNOWLEDGE_PIPELINE.md)
- [Reference knowledge settings](../../../src/cuga/configurations/knowledge/knowledge_settings.toml)
