# 0009 — ONE agent: `cuga` as a supervisor over YAML-defined sub-agents

**Date:** 2026-07-15 · **Status:** accepted · **Supersedes (partially):** [0005](0005-runtime-router-over-prebuilt-agents.md)
**Plan + live proof:** [../plans/SUPERVISOR_REFACTOR.md](../plans/SUPERVISOR_REFACTOR.md)

## Context

The events layer had grown into a multi-agent HOST: 27 independently-addressable agents in a
sqlite registry, agent-picking at the concierge, arm-time binding of flows to specific agents.
That worked (and was fully live-proven), but it forked the agent model from CUGA-main — where a
FastAPI server hosts ONE agent (`cuga-default`), optionally a supervisor with sub-agents from a
canonical YAML config. Two agent-definition interfaces and two routing shapes was one too many.

## Decision

Retire the fleet. There is exactly **one addressable agent: `cuga`**, in both modes:

- `EVENTS_SUPERVISOR=1` → `cuga` is a **supervisor** (CUGA-main's own machinery) whose sub-agents
  load from `supervisor_agents.yaml` (canonical `load_supervisor_config` schema). It picks the
  right specialist **per wake-up** from HANDLES hints generated out of the trigger registry; the
  answer bubbles up. Sub-agents are skills — no channels, no credentials, not addressable.
- Unset → `cuga` is the plain classic agent, as main ships it.

The concierge keeps NL→Flow **compilation** (kind/trigger/slots/validation/dedup — ask-till-legit)
but does **zero agent routing**; every flow and hand-off targets `cuga`. Adding a sub-agent =
editing the YAML + `make reload` (no API; `POST/PUT /api/events/agents` → 410, `GET` = read-only
roster view).

## Consequences

- Routing moved from compiled arm-time bindings to a per-fire LLM pick → it is now a **measured
  gate**: `make test-delegation` (first full run 14/14, 0 self-answers) + offline roster gates
  (every registry trigger claimed; no stale hints).
- Latency: one supervisor inference per wake-up (~3–10s overhead; GitHub 14-trigger live harness
  504s vs 91s fleet-era).
- Two canonical-loader fixes were required upstream-compatible: per-delegate tool scoping
  (CombinedToolProvider app_names) and headless delegates (`auto_load_policies=False` default).
- Fleet-era suites (`test-suite-now`/`test-matrix`/`test-fire`) are auto-skipped in supervisor
  mode pending rework (ROADMAP §not-yet-vetted).
