# Agent Architecture & Contribution Guide (`AGENTS.md`)

Welcome to the **Cuga Agent Nodes** directory. This directory is the core of Cuga's agentic reasoning and execution architecture. It houses the workflows, state-graphs, tool providers, and execution environments that power our lightweight single-agent (`CugaLite`) and multi-agent orchestrator (`CugaSupervisor`) systems.

This document describes the high-level architecture of this directory, coding standards (such as keeping files under 1,000 lines), and instructions on how to contribute modularly.

---

## 1. Directory Structure & Key Components

The directory is structured to separate shared core machinery, agent graph orchestrators, and specialized task agents.

```
src/cuga/backend/cuga_graph/nodes/
├── cuga_agent_core/        # Shared core adapter-seam engine & execution utilities
│   ├── graph/             # Node builders and adapter interfaces (e.g., CoreGraphAdapter)
│   ├── execution/         # Code extraction, variable bridges, and todo tracking
│   ├── tools/             # Runtime shell/filesystem tool injection orchestrators
│   └── policy/            # Routing environments (local vs sandboxed sandbox)
│
├── cuga_lite/             # Optimized single-agent graph (LangGraph) with code-execution loops
│   ├── helpers/           # Helper tools (knowledge checks, bind_tools, find_tools)
│   ├── providers/         # Modular tool registrations and direct LangChain adapters
│   ├── tracking/          # Tool-call tracking and execution logging
│   └── executors/         # Execution runtimes (local, native, OpenSandbox, Docker, E2B)
│
├── cuga_supervisor/       # Orchestrator subgraph that delegates tasks to downstream agents
│   └── a2a_protocol.py    # Actor-to-Agent protocol & sub-task dispatching
│
├── api/                   # Specialized API-interaction code and planning agents
├── browser/               # Specialized UI/browser automation and planning agents
├── chat/                  # Conversational agents
├── task_decomposition_planning/  # Pre-flight request analyzer and breakdown planning
├── answer/                # Synthesis agents producing clean user-visible answers
├── save_reuse/            # Skill recording and reuse agents
└── shared/                # Base agent abstractions
```

---

## 2. Core Architectural Philosophy: The Adapter Pattern

To prevent code duplication and drift between the single-agent (`CugaLite`) and multi-agent supervisor (`CugaSupervisor`), we isolate graph-specific behavior behind the **Adapter Pattern**:

1. **`CoreGraphAdapter` (Abstract Base Class)**: Lives in `cuga_agent_core/graph/graph_nodes.py`. It defines abstract hooks for message handling, step limit resolutions, personal instruction injections, metadata management, and post-invocation side-effects.
2. **`build_agent_graph`**: Lives in `cuga_agent_core/graph/shared_graph.py`. It builds a canonical 3-node LangGraph structure:
   ```
   START ──> prepare ──> call_model <──> execute (sandbox / tool loop) ──> END
   ```
   The builder is 100% graph-agnostic; it is parameterized entirely by the adapter and nodes provided at construction time.
3. **`AgentGraphAdapter` & `SupervisorGraphAdapter`**: Concrete implementations that customize the execution loop for single-agent coding loops or supervisor delegation loops respectively.

---

## 3. Strict Contribution Standards: Code Modularity & Line Limits

To ensure high maintainability, readability, and ease of testing, all contributions must strictly adhere to the following code organization rules:

### ⚠️ The Under-1000-Line Rule
* **No single file in this directory should exceed 1,000 lines of code if possible.**
* Large monolithic files are a severe anti-pattern. They complicate unit testing, increase git conflicts, and obscure architectural seams.
* If a module or adapter begins to approach or exceed this limit, it is **mandatory** to refactor and extract cohesive sub-components into sub-packages.

### 🧩 Phased Modularization & Sub-packages
When extending logic, do not append code to existing root-level files. Instead:
1. **Extract Helpers into Sub-directories**:
   Create a dedicated sub-folder (e.g., `helpers/`, `providers/`, `tracking/`) with an `__init__.py` to organize related files cleanly.
2. **Expose Interfaces via Packaged Packages**:
   Use `__init__.py` file exports to keep imports elsewhere in the application clean (e.g., importing from `cuga_lite.providers` rather than raw file paths).
3. **Minimize Cross-Module Coupling**:
   Ensure downstream layers (like `cuga_supervisor`) never depend on the internal helper files of upstream layers (like `cuga_lite`). Shared schemas (like `todos.py`) must live in `cuga_agent_core`.

---

## 4. How to Contribute & Reorganize Code

When adding a new feature, optimizing an execution node, or restructuring files, follow this sequence of steps to maintain system stability:

### Step 1: Design Clean Interfaces
If adding behavior, check if it fits within the `CoreGraphAdapter` hook system. If it does, add a default no-op implementation to `CoreGraphAdapter` and override it only in the target concrete subclass.

### Step 2: Extract and Isolate
Write lightweight, highly specialized modules with singular responsibilities.
For example, instead of writing database-access logic directly inside a node:
* Define a base interface class.
* Implement a concrete handler class in a `providers/` sub-package.
* Bind the concrete handler at graph assembly time.

### Step 3: Run the Code Quality Pipeline
We use `ruff` for extremely fast linting and formatting. Run these commands before committing any Python changes:
```bash
# Auto-format files to adhere to project standards
uv run ruff format src/cuga/backend/cuga_graph/nodes/

# Perform lint checks and catch potential errors
uv run ruff check src/cuga/backend/cuga_graph/nodes/
```

### Step 4: Run the Unit & Integration Tests
Ensure that your changes do not introduce regressions:
```bash
# Run unit tests for the core engine
uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_agent_core/

# Run unit tests for Cuga Lite
uv run pytest src/cuga/backend/cuga_graph/nodes/cuga_lite/

# Run the complete test suite
uv run pytest
```

Always write new unit tests in the corresponding `tests/` sub-folder (e.g. `cuga_agent_core/tests/graph/`, `cuga_agent_core/tests/execution/`) to verify all new branches and components.
