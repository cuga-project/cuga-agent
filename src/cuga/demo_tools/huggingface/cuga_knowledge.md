# CUGA Knowledge Base

*A compact reference for agents answering questions about the CUGA framework.*

## Overview

**CUGA (Configurable Generalist Agent)** is an open-source agent framework designed for enterprise workflows.
It combines hybrid reasoning (API + web), tool orchestration, policy guardrails, memory, and configurable behavior patterns.

**Why it exists:**
Building robust domain-specific agents from scratch is expensive. CUGA provides a generalist core you configure with your own tools, APIs, policies, and workflows.

---

## Core Concepts

### What CUGA Is

* A **planner → executor** agent engine with code-generation capabilities.
* A **configurable generalist**, not a domain-specific chatbot.
* Designed for **enterprise reliability**, HITL support, and safe execution.
* Modular: tools, policies, memory, and reasoning modes are all replaceable.

### What CUGA Is Not

* Not a single-task bot.
* Not tied to one model or one tool framework.
* Not opinionated on UI—can run headless, in Langflow, HF Spaces, notebooks, or scripts.

---

## Architecture

### Planner

Breaks user intent into sub-tasks; chooses strategies; checks policies.

### Executor

Performs steps, including dynamic code generation via the **code-act** mechanism.

### Code-Act Agent

Generates Python “glue code” to handle:

* API calls
* pagination
* schema-heavy responses
* loops & conditionals
* data aggregation

### Variable Store

Holds intermediate results **outside** LLM context → allows large data without context flooding.

### Task Modes

* `api` – API tools only
* `web` – browser extension
* `hybrid` – both

---

## Capabilities

### Core Abilities

* Hybrid API + web automation
* Multi-step planning & execution
* Tool orchestration through Python, OpenAPI, LangChain, or MCP
* Human-in-the-loop approvals
* Configurable reasoning strategies (fast, balanced, accurate)

### Advanced / Experimental

* Policy-aware planning
* Saving successful plans or code snippets
* Early memory layer for reuse
* Exposure of CUGA itself as a tool to other agents

---

## Configuration

### What You Can Configure

* Tools: Python functions, APIs, MCP servers, browser actions
* Reasoning mode: fast/balanced/accurate/custom
* Domain instructions and agent persona
* Safety policies
* Memory backends (optional)

### Domain Adaptation

Customize:

* task prompts
* policy objects
* domain-specific tips for APIs
* workflows (step templates or plan hints)

---

## Tools & Integrations

### Supported Tool Types

* **OpenAPI** schemas (auto-parsed)
* **Python functions / classes**
* **LangChain tools**
* **MCP servers**
* **Browser Automation** (web task mode)
* Custom tools via simple Python wrappers

### Ecosystem Integrations

* **Langflow**: low-code visual builder, CUGA block
* **Hugging Face Spaces**: interactive demo
* **Other agents**: CUGA can be exposed as a tool

---

## Benchmarks

### Performance

* **🥇 #1 on AppWorld** (750 tasks, 457 APIs)
* **Top-tier on WebArena**, #1 from Feb–Sep 2025

### Why It Matters

These benchmarks validate CUGA’s:

* generalization across real enterprise tasks
* hybrid reasoning reliability
* stability across thousands of workflows

---

## Policy & Safety

### Policy Layer

CUGA enforces:

* Allowed/forbidden actions
* Scope-of-intent classification
* Data boundaries
* When HITL approval is needed
* Organizational vs. user-level policy hierarchy

### Safety Behaviors

* Can refuse unsafe or out-of-scope tasks
* Can ask for clarification or approval
* Supports auditability via logs and structured steps

---

## Memory

### What CUGA Can Remember (Experimental)

* Successful code snippets
* Plans & execution traces
* API schemas and patterns
* User preferences
* Domain documents (optional)

### Why Memory Matters

* Faster task repetition
* Higher accuracy
* Lower hallucination risk
* Trustworthiness through predictable reuse

---


## Roadmap

Planned improvements include:

* Stronger policy governance 
* Long-term memory persistence and retrieval
* Learning from demonstrations and prior trajectories
* Multi-agent orchestration patterns
