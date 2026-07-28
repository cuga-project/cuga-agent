# Agent Skills — packaging capability for autonomous agents

Audience: platform engineers and technical leads building agent systems

Preferences:
- Tone: precise, concrete, opinionated where the evidence supports it
- Length: ~6 slides

## Cover
Title: "Agent Skills". Subtitle: "Packaging a capability so an agent can pick it up on its own." Footer: "Platform engineering · 2026".

## The problem with stuffing everything in the prompt
Why system prompts stop scaling once an agent has more than a handful of capabilities. [[render as a before/after, two equal panels side by side]]

**Left — everything in the system prompt:**
- Every procedure competes for the same context window, whether or not the task needs it.
- Token cost is paid on every single turn, including trivial ones.
- Editing one workflow risks perturbing the model's behaviour on all the others.
- Nobody can tell which instructions actually fired for a given answer.

**Right — skills loaded on demand:**
- The prompt carries only a one-line description per capability.
- The full playbook is fetched only when a task matches it.
- Each skill is versioned and tested independently of the agent.
- What the agent read is explicit and auditable after the fact.

## What a skill actually is
Definition slide. [[render as a two-pane definition layout]]

**Term:** Agent Skill.

**Body:** A directory containing a SKILL.md file — YAML frontmatter with a name and a description, followed by a markdown body — plus any companion scripts or reference documents the workflow needs. The host discovers skills at startup and advertises only the descriptions in the system prompt. When a task matches, the model calls a load_skill tool and receives the full body as its instructions. The description is what the model routes on; the body is what it then follows. Everything heavy stays outside the model.

## The three-part contract
What every skill must get right to be usable by an agent. [[render as a three-column pillar layout, equal weight]]

- **Discovery** — The description carries explicit trigger phrases. If it does not say the words a user would say, the model never reaches for it, no matter how good the body is.
- **Instruction** — The body is a procedure, not a reference manual. It states the order of operations, names the exact commands, and calls out the failure modes worth stopping on.
- **Execution** — Companion files ship alongside and land in the agent's sandbox, so the workflow can actually run rather than merely being described.

## Step limits change the shape of the instructions
The single constraint that most often breaks a skill in practice. [[render as a stat callout with supporting detail]]

**Stat:** 30 seconds

**Detail:** A typical agent sandbox kills a single step after roughly thirty seconds, while real work — rendering a document, training a probe, running a suite — takes minutes. A skill that says "run this and wait" gets killed halfway and reports a false failure. The fix is structural, not cosmetic: expose a way to start work in the background, poll it in bounded slices, and collect the result. Skills that ignore this look correct in review and fail on first contact.

## What to remember
Closing summary. [[render as a numbered takeaway list]]

- A skill is discovered by its description and judged by its body — invest in both.
- Keep the heavy dependencies on a service; ship the agent a thin client.
- Design for a step limit from the start, or the workflow will not survive one.
- Generate the skill from the code it wraps, so the two cannot drift apart.
