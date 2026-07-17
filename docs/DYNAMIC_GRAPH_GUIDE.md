# Understanding CUGA's Dynamic Graph

## Quick Summary

CUGA's **dynamic graph** is a [LangGraph](https://langchain-ai.github.io/langgraph/)-powered state machine that orchestrates multiple specialized agent nodes. It's "dynamic" because:

1. **Runtime routing**: Nodes use `Command(goto=...)` to dynamically choose the next node based on state
2. **Conditional execution**: Each node decides where to route next, not predetermined edges
3. **Subgraph composition**: Complex agent logic (CugaLite, CugaSupervisor) run as compiled subgraphs within the main graph
4. **State-driven flow**: All decisions flow from the `AgentState` object that gets updated at each step

---

## 1. Core Entry Point: `DynamicAgentGraph`

**Location**: `src/cuga/backend/cuga_graph/graph.py`

The `DynamicAgentGraph` class is the main orchestrator:

```python
class DynamicAgentGraph:
    def __init__(self, configurations, ...):
        # Initialize 27+ agent nodes
        self.task_decomposition_agent = TaskDecompositionNode(...)
        self.plan_controller_agent = PlanControllerNode(...)
        self.cuga_lite = CugaLiteNode(...)  # Subgraph for complex tasks
        self.cuga_supervisor = CugaSupervisorNode(...)  # Multi-agent coordination
        # ... many more nodes
        
    async def build_graph(self):
        # 1. Create LangGraph StateGraph
        graph = StateGraph(AgentState)
        
        # 2. Add all nodes
        await self.add_nodes(graph)
        
        # 3. Connect edges (static) and conditional routing (dynamic)
        self.add_edges(graph)
        
        # 4. Compile with checkpoint memory
        self.graph = graph.compile(...)
```

### Key Method: `build_graph()`

This is called once when CUGA initializes. It:

1. Creates a `StateGraph` using the shared `AgentState` schema
2. Adds 20+ individual nodes (ChatAgent, TaskAnalyzer, APIPlanner, etc.)
3. Adds 2 subgraphs: **CugaLiteSubgraph** and **CugaSupervisor** (if enabled)
4. Compiles with memory checkpointer for interrupts (human-in-the-loop)

---

## 2. The Shared State: `AgentState`

**Location**: `src/cuga/backend/cuga_graph/state/agent_state.py`

`AgentState` is the single source of truth that flows through the entire graph:

```python
class AgentState(BaseModel):
    # Input/Output
    input: str                          # User's original request
    final_answer: str                   # The answer to return
    
    # Conversation history
    messages: List[BaseMessage]         # All LLM messages (ChatGPT-style)
    chat_agent_messages: List[...] = [] # Chat agent's conversation thread
    
    # Planning & decomposition
    task_analysis: AnalyzeTaskOutput    # Analysis of the task (intent, etc.)
    task_decomposition: TaskDecompositionPlan  # Break task into subtasks
    plan: List[Dict]                    # Current plan for execution
    
    # Variables collected during execution
    variables_manager: VariablesManager # Named variables (tool results, etc.)
    
    # Tool tracking
    tool_call_records: List[ToolCallRecord]  # Audit trail of tool calls
    
    # Human-in-the-loop
    hitl_action: Optional[FollowUpAction]    # Human action to approve
    hitl_response: Optional[ActionResponse]  # Human's response
    
    # Navigation
    sender: str                         # Which node sent the message here
    # ... 20+ more fields
```

**Key insight**: Every node reads from this state and writes updates to it. LangGraph merges updates with `state.model_dump()`.

---

## 3. Node Types & Their Routing

### A. Simple Linear Nodes

These make a decision and route to one next node:

| Node | What it does | Routes to |
|------|-------------|-----------|
| **ChatAgent** | Processes conversational queries; may route to Task Analyzer or directly to Final Answer | TaskAnalyzerAgent, SuggestHumanActions, or FinalAnswerAgent |
| **TaskAnalyzer** | Breaks down the task; decides which agent can best handle it | CugaLite (for code), CugaSupervisor, Browser, API planner, etc. |
| **FinalAnswerAgent** | Formats and returns the answer | END |
| **PlanController** | Manages task execution plan and coordinates agents | Multiple agents or Final Answer |

**Example (from [chat.py:139](../src/cuga/backend/cuga_graph/nodes/chat/chat.py#L139))**:

```python
async def node_handler(state: AgentState, ...) -> Command:
    if state.sender == "WaitForResponse":  # Human approved an action
        return Command(update=state.model_dump(), goto="FinalAnswerAgent")
    
    if not settings.features.chat:  # Chat disabled
        return Command(update=state.model_dump(), goto="TaskAnalyzerAgent")
    
    # ... process chat, then decide where to route
    if res.tool_calls and requires_approval(res.tool_calls[0]):
        return Command(goto="SuggestHumanActions")  # Ask human first
    
    return Command(goto="FinalAnswerAgent")  # Send answer
```

Each node returns a **`Command` object** that tells LangGraph where to go next. This is how routing is "dynamic"—decided at runtime.

### B. Complex Subgraph Nodes

These are more like workflows:

#### **CugaLiteSubgraph**

Used for complex multi-step reasoning, code generation, and tool use:

- **When triggered**: TaskAnalyzer decides this is a coding/multi-step task
- **Structure**: Itself a LangGraph with 5-7 internal nodes (ReflectionAgent, CodeExecutor, etc.)
- **Flow**: CugaLiteSubgraph → CugaLiteCallback
- **Callback node**: Processes results and decides next step
- **Code location**: `src/cuga/backend/cuga_graph/nodes/cuga_lite/cuga_lite_graph.py`

#### **CugaSupervisorSubgraph** (if enabled)

Orchestrates multiple specialized agents (sales agent, support agent, etc.):

- **When triggered**: If supervisor mode is on and task requires multi-agent coordination
- **Strategies**: sequential, parallel, or adaptive execution
- **Flow**: CugaSupervisorSubgraph → CugaSupervisorCallback
- **Code location**: `src/cuga/backend/cuga_graph/nodes/cuga_supervisor/cuga_supervisor_graph.py`
- **Config**: YAML file defining which agents + their tools
- **Status**: See [design.md](../design.md) for full architecture

---

## 4. Main Graph Flow (Simplified)

```
START
  ↓
ChatAgent (conversational entry point)
  ↓ (routes to one of:)
  ├─→ FinalAnswerAgent → END (simple chat response)
  ├─→ TaskAnalyzerAgent (need to decompose)
  └─→ SuggestHumanActions (need human approval)
        ↓
        WaitForResponse (pause for human)
        ↓
    TaskAnalyzerAgent (after approval, what to do?)
        ↓ (TaskAnalyzer routes to one of:)
        ├─→ CugaLiteSubgraph (coding, complex reasoning)
        ├─→ CugaSupervisorSubgraph (multi-agent, if enabled)
        ├─→ BrowserPlannerAgent (web browsing)
        ├─→ APIPlanner (API calls)
        ├─→ SaveReuseAgent (reuse saved flows)
        └─→ FinalAnswerAgent (direct answer)
        ↓ (results converge back through callbacks)
        ↓
    PlanControllerAgent (manage overall plan)
        ↓
    FinalAnswerAgent
        ↓
       END
```

**Key flows**:
- **Simple**: Chat → Final Answer → END (1-2 steps)
- **Code/Complex**: Chat → TaskAnalyzer → CugaLite → Callback → PlanController → Final Answer → END
- **Multi-step**: Chat → TaskAnalyzer → [Browser/API/Supervisor] → Plan Controller → Final Answer → END

---

## 5. How to Navigate & Debug the Graph

### 5.1 Trace Execution Step-by-Step

**What to check**:
1. **Start here**: `src/cuga/backend/cuga_graph/graph.py:116` (`build_graph()`)
2. **Look at node routing**: Each node's `node_handler()` or `invoke()` method
3. **Understand the decision**: Search for `Command(goto=...)` in that node
4. **Follow the state**: Read `state.sender`, `state.task_analysis`, `state.task_decomposition`

**Example debuggging path**:
```python
# 1. User sends "write Python code to fetch from API"
# 2. Entry: ChatAgent.node_handler()
#    → Recognizes as task, routes to TaskAnalyzerAgent

# 3. TaskAnalyzer.node_handler() analyzes the input
#    → Sees it's code generation + API, sets state.task_analysis
#    → Decides: "CugaLite is best for this" → goto "CugaLite"

# 4. CugaLiteSubgraph.invoke()
#    → Internal flow: code_planner → executor → reflection
#    → Generates code, executes it, validates

# 5. CugaLiteCallback.node_handler()
#    → Processes results, decides next
#    → If more steps needed: goto PlanController
#    → If done: goto FinalAnswerAgent

# 6. PlanControllerAgent → FinalAnswerAgent → END
```

### 5.2 Key File Structure for Navigation

```
src/cuga/backend/cuga_graph/
├── graph.py                          # Main orchestrator (read this first!)
├── state/
│   └── agent_state.py               # Shared state schema
├── nodes/
│   ├── chat/chat.py                 # Entry point node
│   ├── task_decomposition_planning/
│   │   ├── task_decomposition.py    # Plans overall task
│   │   ├── task_analyzer_agent/     # Decides which agent to use
│   │   └── plan_controller.py       # Manages execution plan
│   ├── cuga_lite/
│   │   ├── cuga_lite_graph.py       # Subgraph for complex reasoning
│   │   └── cuga_lite_node.py        # Entry node for subgraph
│   ├── cuga_supervisor/
│   │   ├── cuga_supervisor_graph.py # Subgraph for multi-agent
│   │   └── cuga_supervisor_node.py  # Entry node for subgraph
│   ├── browser/                     # Browser automation agents
│   ├── api/                         # API planning & calling agents
│   ├── human_in_the_loop/           # Approval workflow
│   └── ...
└── policy/                          # Policy/safety checks
```

### 5.3 Debug Checklist

1. **Is the state being passed correctly?**
   - Every Command must include `state.model_dump()`
   - Check: `return Command(update=state.model_dump(), goto="NextNode")`

2. **Where is routing failing?**
   - Add breakpoint in the node's `node_handler()`
   - Print: `state.task_analysis`, `state.sender`, `state.input`
   - Look for exception in the conditional logic

3. **Is a subgraph being triggered?**
   - Check if "CugaLiteSubgraph" or "CugaSupervisorSubgraph" appears in the flow
   - Subgraphs have their own internal nodes—trace into their `graph.py`

4. **Memory checkpoints?**
   - Interrupts happen at: `action_agent` and `interrupt_tool_node`
   - This pauses execution for human-in-the-loop approval

---

## 6. The Three Categories of Nodes

### **Category 1: LLM-Based Decision Nodes**
- Use an LLM to analyze state and decide routing
- Examples: ChatAgent, TaskAnalyzer, PlanController
- Pattern: `await agent.invoke(state) → Command(goto=...)`

### **Category 2: Action/Execution Nodes**
- Execute a tool, run code, browse web, etc.
- Examples: BrowserPlanner, APIPlanner, ActionAgent
- Pattern: Execute → update state → route to next

### **Category 3: Utility Nodes**
- Format results, wait for human, manage state
- Examples: FinalAnswerAgent, WaitForResponse, Callbacks
- Pattern: Transform state → possibly route next or END

---

## 7. State Flow Example

### **Scenario: "Send an email to all customers with balance > $1000"**

```python
# Step 1: ChatAgent receives input
state.input = "Send an email to all customers with balance > $1000"
state.sender = "ChatAgent"

# Step 2: ChatAgent → TaskAnalyzer
# (ChatAgent routes because it recognizes this needs task decomposition)
state.sender = "ChatAgent"
# Command(goto="TaskAnalyzerAgent")

# Step 3: TaskAnalyzer analyzes the intent
state.task_analysis = {
    "intent": "Send bulk email based on criteria",
    "requires": ["API call to get customers", "CRM email tool"],
    "complexity": "medium"
}
state.sender = "TaskAnalyzer"
# Decides: This needs API calls → CugaLite (or APIPlanner)
# Command(goto="CugaLite")

# Step 4: CugaLiteSubgraph
# (Internal nodes: plan → code → execute → verify)
# Generates code to query customer database, filters by balance, sends emails
state.variables_manager.add_variable(
    {"success": 47, "failed": 2},
    "email_results",
    "Results of bulk email send"
)
state.sender = "CugaLiteCallback"
# Command(goto="PlanController")

# Step 5: PlanController checks if plan is complete
# (In this case, task is done)
state.final_answer = "Successfully sent emails to 47 customers with balance > $1000. 2 failed due to invalid addresses."
state.sender = "PlanController"
# Command(goto="FinalAnswerAgent")

# Step 6: FinalAnswerAgent formats answer and routes to END
state.sender = "FinalAnswerAgent"
# Command(goto=END)

# Step 7: User receives answer
```

---

## 8. Advanced Topics

### **Dynamic Subgraph Compilation**

Both CugaLite and CugaSupervisor are compiled at `build_graph()` time (not runtime):

```python
# In graph.py:add_nodes()
cuga_lite_subgraph = create_cuga_lite_graph(...)
compiled_subgraph = cuga_lite_subgraph.compile()
graph.add_node("CugaLiteSubgraph", compiled_subgraph)
```

This means:
- ✅ Subgraph structure is fixed at build time
- ✅ But subgraph nodes read dynamic config at runtime (via `config["configurable"]`)
- ✅ Policy system and tool provider are passed via configurable dict

### **Policy & Tool Guarding**

Tool access is gated via `PolicyConfigurable`:

1. Every tool call goes through `ToolGuard`
2. Checks: Is user allowed? Is this tool safe?
3. Lives in: `src/cuga/backend/cuga_graph/policy/`

### **Checkpoint & Interrupt**

LangGraph checkpoint mechanism allows pausing mid-execution:

```python
graph.compile(
    checkpointer=MemorySaver(),
    interrupt_after=["action_agent", "interrupt_tool_node"]
)
```

When interrupted:
1. Graph pauses at specified nodes
2. Human approves/rejects
3. `WaitForResponse` node resumes with human's choice
4. Execution continues

---

## 9. Calling the Dynamic Graph: Supervisor ON vs OFF

### 9.1 How the Graph Gets Invoked

Every invocation follows this pattern:

```python
# Build phase (happens once at startup)
dag = DynamicAgentGraph(configurations, ...)
await dag.build_graph()

# Invoke phase (happens per user request)
initial_state = AgentState(input="user query", ...)
config = dag.get_config_with_policy({"thread_id": "session_123"})
result = await dag.graph.ainvoke(initial_state, config=config)
```

The `config` dict is critical—it contains:
- `configurable["policy_system"]`: Controls tool access, safety checks
- `configurable["special_instructions"]`: Custom agent instructions
- `thread_id`: Session continuity for checkpoints

---

### 9.2 Supervisor ENABLED: The Difference in Execution

**Config setup**:
```python
# settings.yaml or environment
supervisor:
  enabled: true
  config_path: "config/supervisor_config.yaml"  # Optional: agents + strategies
  # If config_path not provided, uses default 3-agent demo
```

**What happens at `build_graph()` time**:
```python
# graph.py:276-397
if getattr(settings.supervisor, 'enabled', False):
    # Create real supervisor subgraph with multiple agents
    supervisor_subgraph = create_cuga_supervisor_graph(
        supervisor_model=model,
        agents=agents,  # CRM agent, Email agent, Filesystem agent, etc.
        special_instructions=...
    )
    compiled_supervisor_subgraph = supervisor_subgraph.compile()
    graph.add_node("CugaSupervisorSubgraph", compiled_supervisor_subgraph)
    graph.add_node("CugaSupervisorCallback", self.cuga_supervisor.callback_node)
```

**Execution path when user sends query**:
```
User: "Send emails to all customers with balance > $1000 AND update their CRM records"
    ↓
ChatAgent
    ↓
TaskAnalyzer.should_use_supervisor_mode() → TRUE (enabled)
    ↓ Command(goto="CugaSupervisor")
CugaSupervisorSubgraph.invoke()
    │
    ├─→ prepare_agents: Register CRM, Email, Filesystem agents
    ├─→ delegate_task: LLM decides → use CRM agent + Email agent (parallel)
    ├─→ execute_agents: CRM agent queries customers; Email agent sends emails (concurrently)
    │   - CRM agent uses its tools: get_customers, filter by balance
    │   - Email agent uses its tools: send_email, get_templates
    ├─→ collect_variables: Gather results from both agents
    ├─→ aggregate_results: Merge 47 emails sent + 2 CRM updates
    └─→ synthesize_response: Generate natural language summary
    ↓
CugaSupervisorCallback (processes results)
    ↓
PlanController (checks if plan complete)
    ↓
FinalAnswerAgent (formats answer)
    ↓
END: "Sent 47 emails and updated 2 CRM records"
```

**Key difference**: Multi-agent orchestration handles the entire task without falling back to CugaLite.

---

### 9.3 Supervisor DISABLED (Default): The Difference in Execution

**Config setup**:
```python
# settings.yaml or environment (default)
supervisor:
  enabled: false  # or omitted entirely
```

**What happens at `build_graph()` time**:
```python
# graph.py:398-405
else:
    # Create a STUB that just routes to CugaLite
    async def _cuga_supervisor_stub(state, config=None):
        return Command(update=state.model_dump(), goto="CugaLite")
    
    graph.add_node("CugaSupervisor", _cuga_supervisor_stub)
    # NO CugaSupervisorCallback added
    # NO supervisor subgraph created
```

**Execution path when user sends same query**:
```
User: "Send emails to all customers with balance > $1000 AND update their CRM records"
    ↓
ChatAgent
    ↓
TaskAnalyzer.should_use_supervisor_mode() → FALSE (disabled)
    ↓ (supervisor check skipped entirely)
TaskDecompositionAgent (breaks task into subtasks)
    ↓ Command(goto="CugaLite")
CugaLiteSubgraph.invoke()
    │
    ├─→ Planner: Plans code to execute both operations
    ├─→ Code generator: Writes Python code for:
    │   - Query customer DB (filter by balance)
    │   - Send bulk emails
    │   - Update CRM records
    ├─→ Executor: Runs the code in sandbox
    ├─→ Reflection: Validates results, handles errors
    └─→ Result aggregation
    ↓
CugaLiteCallback (processes results)
    ↓
PlanController (checks plan status)
    ↓
FinalAnswerAgent (formats answer)
    ↓
END: "Sent 47 emails and updated 2 CRM records"
```

**Key difference**: Single agent (CugaLite) does everything via code generation instead of agent delegation.

---

### 9.4 Side-by-Side Comparison

| Aspect | Supervisor ON | Supervisor OFF |
|--------|---------------|-----------------|
| **Build time** | Creates real subgraph + 3+ agents | Creates stub function only |
| **Build time cost** | Higher (agent initialization) | Lower (just a stub) |
| **Routing decision** | TaskAnalyzer → CugaSupervisor | TaskAnalyzer → TaskDecomposition → CugaLite |
| **Execution model** | Multi-agent delegation + coordination | Single-agent code generation |
| **Tool access** | Per-agent tools (CRM tools, Email tools) | Shared tool provider (CugaLite tools) |
| **Parallelization** | Agents can run in parallel | Execution is sequential in code |
| **Task understanding** | Supervisior LLM decides agent delegation | TaskDecomposition + CugaLite LLM decide approach |
| **Error handling** | Agent fails independently, supervisor retries | Code fails, reflection agent debugs |
| **Subgraph nodes** | ~7 nodes (prepare, delegate, execute, collect, aggregate, synthesize, finalize) | ~5 nodes (plan, code_gen, executor, reflection, callback) |

---

### 9.5 When TaskAnalyzer Routes to CugaSupervisor

**Location**: `src/cuga/backend/cuga_graph/nodes/task_decomposition_planning/analyze_task.py:250`

```python
async def node_handler(state: AgentState, ...) -> Command:
    # First check: is supervisor enabled?
    if await TaskAnalyzer.should_use_supervisor_mode(state):
        logger.info("Supervisor mode enabled - routing to CugaSupervisor")
        return Command(update=state.model_dump(), goto="CugaSupervisor")
    
    # Second check: is lite mode (CugaLite fast path) enabled?
    if await TaskAnalyzer.should_use_fast_mode_early(state):
        logger.info("Fast mode enabled - checking tool threshold")
        return Command(update=state.model_dump(), goto="CugaLite")
    
    # Default: normal flow with task decomposition
    return Command(update=state.model_dump(), goto="TaskDecompositionAgent")
```

**Priority order** (from `analyze_task.py:249-256`):
1. **Check supervisor FIRST** (highest priority if enabled)
2. **Check lite mode (CugaLite fast)** if supervisor off
3. **Fall back to TaskDecomposition** if both off

---

### 9.6 What State Flows Through: ON vs OFF

**Supervisor ON - State after CugaSupervisorSubgraph**:
```python
state.supervisor_chat_messages = [...]  # Supervisor's conversation
state.agent_variables = {
    "crm_agent_results": {...},
    "email_agent_results": {...}
}
state.supervisor_variables_manager.variables = {
    "crm_agent_customers": [...],
    "email_agent_sent_count": 47
}
state.final_answer = "Sent 47 emails and updated CRM"
state.sender = "CugaSupervisorCallback"
```

**Supervisor OFF - State after CugaLiteSubgraph**:
```python
state.messages = [...generated_code..., ...execution_result...]
state.cuga_lite_final_answer = "Sent 47 emails and updated CRM"
state.variables_manager.variables = {
    "execution_result": {...}
}
state.final_answer = "Sent 47 emails and updated CRM"
state.sender = "CugaLiteCallback"
```

Both arrive at PlanController with similar state, but the **source and path** are different.

---

### 9.7 Configuration Example

**Enable Supervisor with Custom Agents** (`settings.yaml`):
```yaml
supervisor:
  enabled: true
  config_path: "config/my_agents.yaml"  # YAML file with agents definition
  # Optional: model override for supervisor
  model:
    provider: openai
    model: gpt-4o
```

**My Agents YAML** (`config/my_agents.yaml`):
```yaml
supervisor:
  strategy: adaptive  # sequential, parallel, adaptive
  mode: plan_upfront  # plan_upfront or conversational
  description: "Coordinator for CRM and Email tasks"

agents:
  - name: crm_agent
    type: internal  # Internal CugaAgent
    tools:
      - get_customers
      - update_customer
  
  - name: email_agent
    type: internal
    tools:
      - send_email
      - get_templates
```

**Disable Supervisor** (`settings.yaml`):
```yaml
supervisor:
  enabled: false  # Default
  # No other supervisor settings needed
```

---

### 9.8 Practical Debugging: How to Tell Which Path Was Taken

**Check logs**:
```
# If you see this, supervisor was used:
INFO: Supervisor mode enabled - routing to CugaSupervisor
INFO: Loaded 3 agents from supervisor config

# If you see this, CugaLite was used:
INFO: Fast mode enabled - checking tool threshold
DEBUG: CugaLiteSubgraph invoked

# If you see this, normal flow:
DEBUG: Full task analysis running
```

**Check state at PlanController**:
```python
# Supervisor path
state.sender == "CugaSupervisorCallback"
state.supervisor_variables_manager is not None

# CugaLite path
state.sender == "CugaLiteCallback"
state.cuga_lite_final_answer is not None

# Normal path
state.sender == "TaskDecompositionAgent"
```

---

## 10. Quick Reference: Which Node to Look At?

| Question | File |
|----------|------|
| Where does the graph start? | `graph.py:build_graph()` |
| How does chat work? | `nodes/chat/chat.py` |
| How are tasks broken down? | `nodes/task_decomposition_planning/` |
| How does code execution work? | `nodes/cuga_lite/cuga_lite_graph.py` |
| How do multiple agents coordinate? | `nodes/cuga_supervisor/cuga_supervisor_graph.py` |
| How is human approval handled? | `nodes/human_in_the_loop/` |
| What state flows through? | `state/agent_state.py` |

---

## 11. Key Concepts Summary

| Concept | Meaning |
|---------|---------|
| **DynamicAgentGraph** | Main orchestrator class; builds and manages the graph |
| **StateGraph** | LangGraph's state machine class |
| **AgentState** | The shared Pydantic model that all nodes read/write |
| **Node** | A discrete processing unit (LLM call, tool execution, etc.) |
| **Command** | The return value from a node that says "goto next node" |
| **Subgraph** | A graph within a graph (CugaLite, CugaSupervisor) |
| **Callback** | Post-processing node that handles subgraph results |
| **Checkpoint** | LangGraph's mechanism for pausing and resuming execution |
| **Configurable** | Runtime config dict passed to nodes (includes policy_system) |

---

## Next Steps

1. **Run a trace**: Set breakpoint in `ChatNode.node_handler()`, send a request, step through
2. **Read the design doc**: [design.md](../design.md) for CugaSupervisor details
3. **Explore a subgraph**: Look at `cuga_lite_graph.py` to see how internal graphs work
4. **Understand the state**: Print `state.model_dump_json()` at each node to see what's flowing

**Happy graph debugging!**

---

## Appendix: Supervisor ON/OFF Visual Flow

### Full Journey Comparison

```
┌─ SUPERVISOR ENABLED ──────────────────────────────────────────┐
│                                                                 │
│  User Input                                                     │
│      ↓                                                          │
│  ChatAgent                                                      │
│      ↓                                                          │
│  TaskAnalyzer                                                   │
│      ↓ (is_supervisor_enabled? YES)                            │
│  ┌─ CugaSupervisorSubgraph ───────────────────────────────┐   │
│  │ ┌─ prepare_agents      (init CRM, Email, FS agents)   │   │
│  │ ├─ delegate_task       (LLM: which agents needed?)    │   │
│  │ ├─ execute_agents      (parallel: get customers,      │   │
│  │ │                       send emails concurrently)     │   │
│  │ ├─ collect_variables   (gather agent results)         │   │
│  │ ├─ aggregate_results   (merge multi-agent outputs)    │   │
│  │ ├─ synthesize_response (generate natural language)    │   │
│  │ └─ finalize            (prepare final_answer)         │   │
│  └──────────────────────────────────────────────────────┘   │
│      ↓                                                          │
│  CugaSupervisorCallback                                        │
│      ↓                                                          │
│  PlanController                                                │
│      ↓                                                          │
│  FinalAnswerAgent                                              │
│      ↓                                                          │
│  END                                                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

┌─ SUPERVISOR DISABLED (Default) ────────────────────────────────┐
│                                                                 │
│  User Input                                                     │
│      ↓                                                          │
│  ChatAgent                                                      │
│      ↓                                                          │
│  TaskAnalyzer                                                   │
│      ↓ (is_supervisor_enabled? NO)                             │
│  TaskDecompositionAgent  (what's the plan?)                    │
│      ↓                                                          │
│  ┌─ CugaLiteSubgraph ─────────────────────────────────┐       │
│  │ ┌─ planner          (plan code to execute both)    │       │
│  │ ├─ code_generator   (write Python code)            │       │
│  │ ├─ executor         (run in sandbox)               │       │
│  │ ├─ reflection       (validate & fix errors)        │       │
│  │ └─ aggregator       (collect results)              │       │
│  └─────────────────────────────────────────────────────┘       │
│      ↓                                                          │
│  CugaLiteCallback                                              │
│      ↓                                                          │
│  PlanController                                                │
│      ↓                                                          │
│  FinalAnswerAgent                                              │
│      ↓                                                          │
│  END                                                           │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

### State Mutations Comparison

**Supervisor ON** - State updates:
```python
state.supervisor_chat_messages ← (grows during delegation/synthesis)
state.agent_chat_messages ← {crm_agent: [...], email_agent: [...]}
state.supervisor_variables_manager.variables ← {
    "crm_agent_customers": [...],
    "email_agent_emails_sent": 47
}
state.sender = "CugaSupervisorCallback"
```

**Supervisor OFF** - State updates:
```python
state.messages ← (grows during planning/coding)
state.variables_manager.variables ← {
    "execution_result": {...},
    "code": "..."
}
state.cuga_lite_final_answer ← "Sent 47 emails"
state.sender = "CugaLiteCallback"
```

### Build-Time vs Runtime

**Build-time** (`DynamicAgentGraph.build_graph()`):
```python
# Supervisor ENABLED
supervisor_subgraph = create_cuga_supervisor_graph(...)
graph.add_node("CugaSupervisorSubgraph", supervisor_subgraph.compile())
graph.add_node("CugaSupervisorCallback", ...)

# Supervisor DISABLED
graph.add_node("CugaSupervisor", _cuga_supervisor_stub)
# ↑ This node never executes at runtime
```

**Runtime** (`dag.graph.ainvoke(state, config)`):
```python
# Supervisor ENABLED
→ TaskAnalyzer: is_supervisor_enabled? YES
→ goto="CugaSupervisor" 
→ CugaSupervisorSubgraph.invoke() [REAL EXECUTION]
→ CugaSupervisorCallback

# Supervisor DISABLED
→ TaskAnalyzer: is_supervisor_enabled? NO
→ goto="TaskDecomposition" [SKIPS SUPERVISOR ENTIRELY]
→ TaskDecompositionAgent
→ goto="CugaLite" (or other branch)
→ CugaLiteSubgraph.invoke() [REAL EXECUTION]
```
