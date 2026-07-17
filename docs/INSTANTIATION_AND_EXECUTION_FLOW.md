# Complete Lifecycle: From Instantiation to Execution

This guide shows **exactly** how DynamicAgentGraph ties together with the supervisor model. Follow this end-to-end.

---

## Phase 1: INSTANTIATION (Build Time)

### Step 1.1: Create the DynamicAgentGraph Instance

```python
# File: your_app.py or main.py
from cuga.backend.cuga_graph.graph import DynamicAgentGraph

# This just instantiates the class - doesn't build the graph yet
dag = DynamicAgentGraph(
    configurations={...},
    langfuse_handler=None,
    policy_system=PolicyConfigurable.get_instance()
)

# At this point:
# ✅ dag.task_decomposition_agent is created (but graph doesn't exist yet)
# ✅ dag.cuga_lite is created (but no subgraph compiled)
# ✅ dag.cuga_supervisor is created (but no subgraph compiled)
# ❌ dag.graph = None (NOT BUILT YET)
```

**What's instantiated**:
- All 27+ **node wrappers** (ChatNode, TaskAnalyzerNode, etc.)
- These nodes are just **Python objects**, not yet connected into a LangGraph

---

### Step 1.2: Build the Graph (`await dag.build_graph()`)

This is the critical step that everything depends on:

```python
# File: your_app.py - called during app startup
await dag.build_graph()

# Now the magic happens:
```

**Inside `build_graph()` - the LangGraph is constructed:**

```python
# 1. Create the LangGraph StateGraph
graph = StateGraph(AgentState)  # All nodes will share this state

# 2. Add all nodes to the graph
await self.add_nodes(graph)
# This adds 20+ nodes: ChatAgent, TaskAnalyzer, CugaLite, etc.

# 3. The CRITICAL PART - supervisor check:

if getattr(settings.supervisor, 'enabled', False):  # Check config
    # ✅ SUPERVISOR ENABLED PATH
    print("Building with SUPERVISOR")
    
    # Create a REAL subgraph with multiple agents
    supervisor_subgraph = create_cuga_supervisor_graph(
        supervisor_model=model,
        agents=agents,  # CRM agent, Email agent, etc.
    )
    compiled_supervisor_subgraph = supervisor_subgraph.compile()
    graph.add_node("CugaSupervisorSubgraph", compiled_supervisor_subgraph)
    graph.add_node("CugaSupervisorCallback", ...)
    # NOW "CugaSupervisor" node is REAL and will execute at runtime
    
else:
    # ❌ SUPERVISOR DISABLED PATH (default)
    print("Building with SUPERVISOR DISABLED (stub)")
    
    # Create a STUB function that never really runs
    async def _cuga_supervisor_stub(state, config=None):
        return Command(update=state.model_dump(), goto="CugaLite")
    
    graph.add_node("CugaSupervisor", _cuga_supervisor_stub)
    # NOW "CugaSupervisor" node exists but is dead code - will never be called

# 4. Add edges (connections between nodes)
self.add_edges(graph)

# 5. COMPILE the entire graph
self.graph = graph.compile(
    checkpointer=MemorySaver(),
    interrupt_after=["action_agent", "interrupt_tool_node"]
)

# NOW dag.graph exists and is ready to use
```

**After `build_graph()` completes:**

```python
dag.graph is not None  # ✅ The compiled LangGraph
dag.graph is a StateGraph  # A state machine with nodes + edges
```

---

## Phase 2: INVOCATION (Runtime - Per User Request)

### Step 2.1: User Sends a Query

```python
# File: your_api.py or chat_handler.py
user_input = "Send emails to all customers with balance > $1000"

# Create initial state
initial_state = AgentState(
    input=user_input,
    messages=[],
    variables_manager=VariablesManager(),
    # ... other fields initialized
)

# Get config with policy system
config = dag.get_config_with_policy({"thread_id": "session_abc123"})
# config now contains:
# {
#     "configurable": {
#         "policy_system": <PolicyConfigurable instance>,
#         "special_instructions": None
#     },
#     "thread_id": "session_abc123"
# }
```

### Step 2.2: Invoke the Graph

```python
# This is THE critical call that runs everything
result = await dag.graph.ainvoke(initial_state, config=config)

# From here on, LangGraph takes over and follows the state machine
```

---

## Phase 3: EXECUTION FLOW (State Machine Running)

Now the **actual execution path depends on supervisor setting**. Here's what happens step-by-step:

### Scenario A: SUPERVISOR DISABLED (Default)

```
┌─ SUPERVISOR DISABLED ─────────────────────────────────────────┐
│                                                                 │
│ [1] START                                                       │
│     ↓                                                          │
│ [2] ChatAgent.node_handler(state, config)                    │
│     • Receives: state.input = "Send emails to..."             │
│     • Processes conversational logic                          │
│     • Decides: task needs decomposition                       │
│     ↓ Command(goto="TaskAnalyzerAgent")                       │
│                                                                │
│ [3] TaskAnalyzer.node_handler(state, config)                │
│     • Checks: should_use_supervisor_mode(state)              │
│     • supervisor_enabled = False (from settings.supervisor)   │
│     • Returns: False ❌ (NOT going to supervisor)              │
│     • Checks: should_use_fast_mode_early(state)              │
│     • Returns: might be True (depends on lite_mode config)    │
│                                                                │
│ [4a] If lite_mode enabled:                                    │
│      ↓ Command(goto="CugaLite")                              │
│      ┌─ CugaLiteSubgraph ────────────────────────┐           │
│      │ Internal nodes:                           │           │
│      │ • Planner: plan code                      │           │
│      │ • CodeGenerator: write Python             │           │
│      │ • Executor: run in sandbox                │           │
│      │ • Reflection: validate results            │           │
│      └───────────────────────────────────────────┘           │
│      ↓ Command(goto="CugaLiteCallback")                      │
│                                                                │
│ [4b] If lite_mode disabled:                                   │
│      ↓ Command(goto="TaskDecompositionAgent")               │
│      (Full task analysis without supervisor)                 │
│                                                                │
│ [5] CugaLiteCallback.node_handler(state, config)           │
│     • Processes CugaLite results                             │
│     • Decides: done or need more steps                       │
│     ↓ Command(goto="PlanController")                         │
│                                                                │
│ [6] PlanController.node_handler(state, config)             │
│     • Checks: is plan complete?                             │
│     ↓ Command(goto="FinalAnswerAgent")                      │
│                                                                │
│ [7] FinalAnswerAgent.node_handler(state, config)           │
│     • Formats final_answer                                   │
│     ↓ Command(goto=END)                                      │
│                                                                │
│ [8] END                                                       │
│     Result returned to user: state.final_answer              │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

**Key point**: TaskAnalyzer checks supervisor, finds it disabled, **skips it entirely**. The stub function is never invoked.

---

### Scenario B: SUPERVISOR ENABLED

```
┌─ SUPERVISOR ENABLED ──────────────────────────────────────────┐
│                                                                 │
│ [1] START                                                       │
│     ↓                                                          │
│ [2] ChatAgent.node_handler(state, config)                    │
│     • Receives: state.input = "Send emails to..."             │
│     • Processes conversational logic                          │
│     • Decides: task needs decomposition                       │
│     ↓ Command(goto="TaskAnalyzerAgent")                       │
│                                                                │
│ [3] TaskAnalyzer.node_handler(state, config)                │
│     • Checks: should_use_supervisor_mode(state)              │
│     • supervisor_enabled = True (from settings.supervisor)    │
│     • Returns: True ✅ (GOING TO SUPERVISOR!)                 │
│     ↓ Command(goto="CugaSupervisor")                         │
│                                                                │
│ [4] CugaSupervisor node invoked                             │
│     (This is the REAL subgraph, built at build_graph() time) │
│     ┌─ CugaSupervisorSubgraph ──────────────────────┐        │
│     │ Internal nodes (from design.md):             │        │
│     │ • prepare_agents: init CRM, Email agents     │        │
│     │ • delegate_task: LLM → which agents?         │        │
│     │ • execute_agents: run agents in parallel     │        │
│     │   - CRM agent: get_customers tool            │        │
│     │   - Email agent: send_email tool             │        │
│     │ • collect_variables: gather results          │        │
│     │ • aggregate_results: merge outputs           │        │
│     │ • synthesize_response: natural language      │        │
│     │ • finalize: prepare final_answer             │        │
│     └────────────────────────────────────────────┘        │
│     ↓ Command(goto="CugaSupervisorCallback")               │
│                                                                │
│ [5] CugaSupervisorCallback.node_handler(state, config)     │
│     • Processes supervisor results                          │
│     • State now has supervisor_variables_manager            │
│     ↓ Command(goto="PlanController")                        │
│                                                                │
│ [6] PlanController.node_handler(state, config)             │
│     • Checks: is plan complete?                             │
│     ↓ Command(goto="FinalAnswerAgent")                      │
│                                                                │
│ [7] FinalAnswerAgent.node_handler(state, config)           │
│     • Formats final_answer                                   │
│     ↓ Command(goto=END)                                      │
│                                                                │
│ [8] END                                                       │
│     Result returned to user: state.final_answer              │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

**Key point**: TaskAnalyzer checks supervisor, finds it enabled, **routes to real subgraph**. Multi-agent orchestration happens.

---

## The Complete Picture: Tying It All Together

### Before Instantiation
```
Nothing exists yet
```

### After Instantiation (before build_graph)
```
dag = DynamicAgentGraph(...)
├── dag.task_decomposition_agent (node object)
├── dag.plan_controller_agent (node object)
├── dag.cuga_lite (node object)
├── dag.cuga_supervisor (node object)
└── dag.graph = None  ❌ NOT YET BUILT
```

### After build_graph()

**If SUPERVISOR DISABLED:**
```
dag.graph = StateGraph (LangGraph)
├── Node: ChatAgent
├── Node: TaskAnalyzer
├── Node: TaskDecompositionAgent
├── Node: CugaLite (wrapper)
│   └── CugaLiteSubgraph (compiled subgraph)
│       └── Internal nodes: planner, code_gen, executor, reflection
├── Node: CugaSupervisor (STUB FUNCTION - dead code)
│   └── _cuga_supervisor_stub() {return goto="CugaLite"}
│       (This node exists but will NEVER be called)
├── Node: PlanController
├── Node: FinalAnswerAgent
└── Edges connecting all nodes
```

**If SUPERVISOR ENABLED:**
```
dag.graph = StateGraph (LangGraph)
├── Node: ChatAgent
├── Node: TaskAnalyzer
├── Node: TaskDecompositionAgent
├── Node: CugaLite (wrapper)
│   └── CugaLiteSubgraph (compiled subgraph)
│       └── Internal nodes: planner, code_gen, executor, reflection
├── Node: CugaSupervisor (REAL NODE - will execute)
│   └── CugaSupervisorSubgraph (compiled subgraph)
│       └── Internal nodes: prepare_agents, delegate_task, execute_agents, etc.
├── Node: CugaSupervisorCallback (added)
├── Node: PlanController
├── Node: FinalAnswerAgent
└── Edges connecting all nodes
```

**The ONLY DIFFERENCE at build time:**
- Supervisor DISABLED: Stub function + no callback
- Supervisor ENABLED: Real subgraph + callback

---

## Step-by-Step: How State Flows Through

### With SUPERVISOR DISABLED:

```python
# Initial state from user
state = AgentState(
    input="Send emails to customers with balance > $1000",
    sender=None,
    variables_manager=VariablesManager(),
    # ... rest of fields
)

# [1] ChatAgent processes
state.sender = "ChatAgent"
state.chat_agent_messages.append(HumanMessage(...))
# Command(goto="TaskAnalyzerAgent")

# [2] TaskAnalyzer processes
state.sender = "TaskAnalyzerAgent"
# Checks: supervisor enabled? NO
# Checks: lite mode? (depends on config)
# Command(goto="CugaLite" or "TaskDecompositionAgent")

# [3] If going to CugaLite:
# CugaLiteSubgraph.invoke(state, config) 
# Returns updated state with:
state.messages.append("generated code...")
state.variables_manager.add_variable(execution_result, ...)
state.sender = "CugaLiteCallback"

# [4] CugaLiteCallback processes
# Command(goto="PlanController")

# [5] PlanController processes
state.sender = "PlanController"
state.final_answer = "Sent 47 emails..."
# Command(goto="FinalAnswerAgent")

# [6] FinalAnswerAgent
# Command(goto=END)

# Result returned
return {
    "final_answer": "Sent 47 emails...",
    "variables": state.variables_manager.variables,
    "sender": "FinalAnswerAgent"
}
```

### With SUPERVISOR ENABLED:

```python
# Initial state from user (SAME)
state = AgentState(
    input="Send emails to customers with balance > $1000",
    sender=None,
    variables_manager=VariablesManager(),
    # ... rest of fields
)

# [1] ChatAgent processes (SAME)
state.sender = "ChatAgent"
# Command(goto="TaskAnalyzerAgent")

# [2] TaskAnalyzer processes (DIFFERENT)
state.sender = "TaskAnalyzerAgent"
# Checks: supervisor enabled? YES ✅
# Command(goto="CugaSupervisor")  ← DIFFERENT DESTINATION!

# [3] CugaSupervisorSubgraph.invoke(state, config)  ← REAL SUBGRAPH
# Internal flow:
#   - prepare_agents()
#   - delegate_task() → decides to use CRM + Email agents
#   - execute_agents() → runs both in parallel
#   - collect_variables()
#   - aggregate_results()
#   - synthesize_response()
#   - finalize()
# Returns updated state with:
state.supervisor_chat_messages.append(...)
state.supervisor_variables_manager.add_variable(...)
state.sender = "CugaSupervisorCallback"

# [4] CugaSupervisorCallback processes
# Command(goto="PlanController")

# [5] PlanController processes
state.sender = "PlanController"
state.final_answer = "Sent 47 emails and updated CRM..."
# Command(goto="FinalAnswerAgent")

# [6] FinalAnswerAgent
# Command(goto=END)

# Result returned (similar but with supervisor variables)
return {
    "final_answer": "Sent 47 emails and updated CRM...",
    "supervisor_variables": state.supervisor_variables_manager.variables,
    "sender": "FinalAnswerAgent"
}
```

---

## The Key Insight

**The entire behavior divergence comes from a single decision point:**

```python
# In TaskAnalyzer.node_handler() - analyze_task.py:250
if await TaskAnalyzer.should_use_supervisor_mode(state):
    return Command(goto="CugaSupervisor")  ← supervisor enabled
else:
    return Command(goto="TaskDecompositionAgent")  ← supervisor disabled
```

This single `if` statement determines:
- ✅ Whether the real CugaSupervisorSubgraph runs
- ✅ Whether agents are orchestrated or code is generated
- ✅ Whether parallel agent execution happens
- ✅ Whether multi-tool coordination is possible

Everything else cascades from that decision.

---

## How DynamicAgentGraph Relates to Supervisor Model

### The Relationship

```
DynamicAgentGraph
    ↓
    build_graph() - reads settings.supervisor.enabled
    ↓
    Creates graph with TWO possible paths:
    ├─ Supervisor ENABLED: Real CugaSupervisorSubgraph included
    └─ Supervisor DISABLED: Stub function (never executed)
    ↓
    graph.ainvoke(state, config) - executes the state machine
    ↓
    TaskAnalyzer checks: is supervisor enabled?
    ├─ YES: goto CugaSupervisorSubgraph (multi-agent)
    └─ NO: goto TaskDecomposition/CugaLite (single-agent code gen)
    ↓
    Result
```

### What "Dynamic" Actually Means

"Dynamic graph" means:
- The **graph structure changes** based on `settings.supervisor.enabled`
- At **build time**, different subgraphs are created
- At **runtime**, different paths are taken based on config
- The same `DynamicAgentGraph` class can produce 2 different execution models

It's not "dynamic" in the sense of "routes changing during execution" (that happens, but that's secondary). It's "dynamic" because **which subgraph and which agents exist depends on config**.

---

## Complete Instantiation Example

```python
# FILE: app.py

import asyncio
from cuga.backend.cuga_graph.graph import DynamicAgentGraph
from cuga.config import settings

async def main():
    # ─────────────────────────────────────────────
    # PHASE 1: INSTANTIATION
    # ─────────────────────────────────────────────
    
    print("Creating DynamicAgentGraph...")
    dag = DynamicAgentGraph(configurations={})
    # Now dag exists but dag.graph is None
    
    print("Building graph...")
    await dag.build_graph()
    # Now dag.graph is a compiled LangGraph
    # Structure depends on settings.supervisor.enabled
    
    # ─────────────────────────────────────────────
    # PHASE 2: RUNTIME
    # ─────────────────────────────────────────────
    
    # User sends query
    from cuga.backend.cuga_graph.state.agent_state import AgentState
    
    user_query = "Send emails to customers with balance > $1000"
    
    initial_state = AgentState(input=user_query)
    config = dag.get_config_with_policy({"thread_id": "session_123"})
    
    print("Invoking graph...")
    result = await dag.graph.ainvoke(initial_state, config=config)
    # This follows ONE of two paths:
    # - Path A (supervisor off): Chat → TaskAnalyzer → CugaLite → Result
    # - Path B (supervisor on): Chat → TaskAnalyzer → CugaSupervisor → Result
    
    print(f"Result: {result['final_answer']}")

if __name__ == "__main__":
    # Check supervisor setting
    print(f"Supervisor enabled: {getattr(settings.supervisor, 'enabled', False)}")
    asyncio.run(main())
```

---

## Summary: How It All Fits

| Phase | What | Why |
|-------|------|-----|
| **Instantiation** | `dag = DynamicAgentGraph()` | Creates node objects |
| **Build** | `await dag.build_graph()` | Creates LangGraph with supervisor subgraph (if enabled) or stub (if disabled) |
| **Runtime** | `await dag.graph.ainvoke(state, config)` | Executes state machine starting at ChatAgent |
| **TaskAnalyzer** | Checks `supervisor_enabled` | Routes to CugaSupervisor (real) or TaskDecomposition (stub skipped) |
| **Supervisor Path** | CugaSupervisorSubgraph | Multi-agent orchestration with delegation |
| **Non-Supervisor Path** | CugaLiteSubgraph | Single-agent code generation |
| **Convergence** | PlanController → FinalAnswer → END | Both paths converge at the same endpoint |

**The supervisor model is BAKED INTO the graph structure at build time.** It's not a runtime plugin—it's a build-time architectural decision that shapes the entire state machine.
