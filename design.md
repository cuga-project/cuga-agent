# CugaSupervisor Design Document

## Overview

This document describes the CugaSupervisor feature implementation - a subgraph system for orchestrating multiple CugaAgent instances. The supervisor pattern allows coordinating specialized agents, each with their own tools, apps, and configurations, similar to how `cuga_lite_graph.py` implements a subgraph.

## Architecture

### Core Components

1. **CugaSupervisor Subgraph** (`cuga_supervisor_graph.py`)
   - Similar structure to `cuga_lite_graph.py`
   - Orchestrates multiple CugaAgent instances
   - Manages task delegation and result aggregation
   - Supports sequential, parallel, and adaptive execution strategies
   - Supports two modes: delegation and response

2. **Supervisor State** (`cuga_supervisor_state.py`)
   - Extends `AgentState` to maintain compatibility
   - **supervisor_chat_messages**: Main chat messages for supervisor (separate from sub-agents)
   - **agent_chat_messages**: Tracks individual sub-agent conversations
   - **supervisor_variables**: Aggregated variables collected from sub-agents
   - **supervisor_mode**: "delegation" or "response"
   - Agent registry, results tracking, and delegation metadata

3. **SDK Support** (`sdk.py`)
   - `CugaSupervisor` class for programmatic creation
   - `from_yaml()` method for YAML configuration loading
   - `invoke()` method for task execution
   - `variables_manager` property for accessing collected variables
   - `add_agent()` and `remove_agent()` for dynamic agent management

4. **YAML Configuration Loader** (`sdk/supervisor_config.py`)
   - Parses YAML configuration files
   - Creates internal CugaAgent instances from config
   - Configures external A2A agents
   - Supports tools, apps, MCP servers, and A2A protocol

5. **A2A Protocol** (`a2a_protocol.py`)
   - Agent-to-Agent communication protocol
   - Supports HTTP, SSE, WebSocket, and STDIO transports
   - Task delegation, result sharing, capability discovery
   - Status checking

6. **Supervisor Node** (`cuga_supervisor_node.py`)
   - Node wrapper for integration with main graph
   - Handles state conversion between AgentState and CugaSupervisorState
   - Callback node for processing results

## Operational Strategies

### 1. Plan Upfront Strategy
- Supervisor decides which agents to use upfront using LLM
- Executes agents with sequential, parallel, or adaptive strategy
- Controls execution flow with LLM decisions between agents (for sequential/adaptive)
- Collects variables from sub-agents
- Synthesizes natural language responses from agent results
- Uses `supervisor_chat_messages` for conversation context
- Best for complex multi-agent coordination tasks

### 2. Conversational Strategy
- Supervisor acts as a single agent with delegation tools
- Conversational approach similar to cuga_lite
- Can call agents via Python code dynamically
- More flexible, agent-driven execution
- Best for simpler tasks or when dynamic agent selection is needed

## State Management

### Supervisor Chat Messages
- **`supervisor_chat_messages`**: The supervisor's own conversation history with the user
- Separate from sub-agents' messages
- Used in plan_upfront strategy for context, task delegation, and response synthesis
- Used in conversational strategy for conversational context
- Maintains continuity across multiple agent invocations

### Variable Management
- **`supervisor_variables`**: Aggregated variables collected from sub-agents
- Each sub-agent maintains its own variables (stored in `agent_variables`)
- Supervisor aggregates variables with agent name prefixes to avoid conflicts
- Accessible via `supervisor_variables_manager` property

## Graph Flow

### Plan Upfront Strategy Flow
```
START -> prepare_agents -> delegate_task -> execute_agents -> 
  collect_variables -> aggregate_results -> synthesize_response -> finalize -> END
```

### Conversational Strategy Flow
```
START -> prepare_agents_and_prompt -> call_model -> 
  [if code: execute_agent_tool -> call_model] -> END
```

### Nodes (Plan Upfront)

1. **prepare_agents**: Initialize and register available agents
2. **delegate_task**: LLM decides which agent(s) to use based on supervisor_chat_messages
3. **execute_agents**: Execute selected agents (sequential/parallel/adaptive) with LLM-controlled flow
4. **collect_variables**: Collect variables from sub-agents into supervisor_variables
5. **aggregate_results**: Combine results from multiple agents
6. **synthesize_response**: Generate final response from sub-agent results
7. **finalize**: Prepare final answer and update supervisor_chat_messages

### Nodes (Conversational)

1. **prepare_agents_and_prompt**: Prepare agents, create delegation tools, and generate prompt
2. **call_model**: Call LLM to generate code or text response
3. **execute_agent_tool**: Execute code with agent delegation tools available

## Agent Types

### Internal CugaAgent
- Created directly as `CugaAgent` instances
- No network overhead
- Direct function calls
- Shared memory space
- Defined in YAML without `a2a_protocol` section

### External A2A Agent
- Connected via HTTP/SSE/WebSocket/STDIO
- Network communication required
- Defined in YAML with `a2a_protocol` section
- Supports authentication and retry policies

## A2A Protocol

### Connection Methods

1. **HTTP Transport** (Recommended for production)
   - RESTful API endpoints
   - JSON message format
   - Example: `http://localhost:8000/a2a`

2. **SSE Transport** (Server-Sent Events)
   - Real-time event streaming
   - Similar to MCP SSE transport
   - Example: `http://localhost:8000/a2a/sse`

3. **WebSocket Transport** (Bidirectional)
   - Full-duplex communication
   - Real-time bidirectional messaging
   - Example: `ws://localhost:8000/a2a/ws`

4. **STDIO Transport** (Local agents)
   - For agents running in same process
   - Direct function calls
   - Example: `local_agent_id`

### How to Connect

```python
from cuga.backend.cuga_graph.nodes.cuga_supervisor.a2a_protocol import A2AProtocol

# HTTP Transport
a2a_agent = A2AProtocol(
    endpoint="http://localhost:8000/a2a",
    transport="http",
    auth={"type": "bearer", "token": "secret-token"},
    timeout=30
)
await a2a_agent.connect()

# Delegate task
result = await a2a_agent.delegate_task(
    target_agent="sales_agent",
    task="Get top accounts",
    context={}
)

await a2a_agent.disconnect()
```

## YAML Configuration

### Example Configuration

```yaml
supervisor:
  strategy: adaptive  # sequential, parallel, adaptive
  mode: plan_upfront  # plan_upfront or conversational
  model:
    provider: openai
    model_name: gpt-4o
  description: "Supervisor for coordinating specialized agents"

agents:
  # Internal CugaAgent (no a2a_protocol section)
  - name: sales_agent
    type: internal
    description: "Handles sales and CRM operations"
    tools:
      - name: get_accounts
        type: langchain
    apps:
      - name: digital_sales
        type: api
        url: https://digitalsales.example.com/openapi.json
    mcp_servers:
      - name: filesystem
        command: npx
        args: ["-y", "@modelcontextprotocol/server-filesystem", "./workspace"]
        transport: stdio
    special_instructions: "Focus on sales operations"

  # External Agent via A2A Protocol
  - name: remote_agent
    type: external
    description: "Remote agent via A2A protocol"
    a2a_protocol:
      enabled: true
      endpoint: http://localhost:8000/a2a
      transport: http
      auth:
        type: bearer
        token: ${A2A_TOKEN}
      capabilities: ["task_delegation", "result_sharing"]

# A2A Protocol Global Configuration
a2a:
  protocol_version: "1.0"
  communication:
    type: http
    timeout: 30
    retry_policy:
      max_retries: 3
      backoff: exponential
```

## SDK Usage

### Programmatic Creation

```python
from cuga import CugaAgent, CugaSupervisor
from langchain_core.tools import tool

@tool
def get_accounts() -> str:
    """Get sales accounts"""
    return "Account data"

sales_agent = CugaAgent(tools=[get_accounts])
data_agent = CugaAgent(tools=[analyze_data])

        supervisor = CugaSupervisor(
            agents={"sales_agent": sales_agent, "data_agent": data_agent},
            strategy="adaptive",
            mode="plan_upfront"
        )

result = await supervisor.invoke("Get sales data and analyze it")
print(result.answer)
```

### YAML Configuration

```python
from cuga import CugaSupervisor

supervisor = await CugaSupervisor.from_yaml("supervisor_config.yaml")
result = await supervisor.invoke("Complex task requiring multiple agents")
print(result.answer)

# Access collected variables
vars_manager = supervisor.variables_manager
variables = vars_manager.get_variable_names()
```

## Execution Strategies

### Sequential
- Execute agents one after another
- Each agent receives results from previous agents
- Best for dependent tasks

### Parallel
- Execute all agents simultaneously
- Faster for independent tasks
- Results aggregated after all complete

### Adaptive
- Currently uses sequential as default
- Future: Dynamic strategy selection based on task complexity and agent capabilities

## File Structure

```
src/cuga/
├── backend/cuga_graph/nodes/cuga_supervisor/
│   ├── __init__.py
│   ├── cuga_supervisor_graph.py      # Main supervisor subgraph
│   ├── cuga_supervisor_state.py      # Supervisor state schema
│   ├── cuga_supervisor_node.py       # Supervisor node wrapper
│   ├── a2a_protocol.py               # A2A protocol implementation
│   └── prompts/
│       ├── supervisor_system.jinja2  # (To be created)
│       └── supervisor_user.jinja2   # (To be created)
├── sdk/
│   └── supervisor_config.py          # YAML loader
└── sdk_core/tests/
    ├── test_supervisor_sdk.py
    ├── test_supervisor_multi_agent.py
    ├── test_supervisor_yaml_config.py
    └── fixtures/
        └── supervisor_config.yaml
```

## E2E Tests

### Test Files

1. **test_supervisor_sdk.py** - SDK integration tests
   - Supervisor creation from YAML
   - Agent registration
   - Task delegation
   - Result aggregation
   - Variable collection

2. **test_supervisor_multi_agent.py** - Multi-agent coordination
   - Sequential execution
   - Parallel execution
   - Adaptive strategy
   - Agent failure handling
   - Delegation vs response modes

3. **test_supervisor_yaml_config.py** - YAML configuration tests
   - YAML parsing
   - Agent configuration loading
   - MCP server integration
   - A2A protocol setup and connection

## Implementation Status

✅ **Completed:**
- CugaSupervisorState with supervisor_chat_messages and variable management
- CugaSupervisor subgraph with all nodes
- A2A protocol implementation (HTTP, SSE, WebSocket, STDIO)
- CugaSupervisorNode wrapper
- CugaSupervisor SDK class with from_yaml() and invoke()
- YAML configuration loader
- E2E tests for SDK, multi-agent coordination, and YAML config

## Future Enhancements

1. Dynamic agent discovery and registration
2. Agent capability learning and optimization
3. Cross-agent variable sharing with permissions
4. Supervisor learning from past delegations
5. Support for nested supervisors (supervisor of supervisors)
6. A2A protocol extensions for streaming responses
7. Agent health monitoring and auto-recovery
8. Prompt templates for supervisor nodes
9. Full MCP server integration in YAML loader
10. Tool loading from YAML definitions

## Integration Points

1. **Main Graph** (`graph.py`): Supervisor subgraph can be integrated similar to CugaLiteSubgraph (optional)
2. **SDK** (`sdk.py`): CugaSupervisor class available alongside CugaAgent
3. **Tool Provider**: Reuses existing tool provider interfaces
4. **State Management**: Compatible with AgentState for seamless integration
5. **Variables Manager**: Reuses StateVariablesManager for supervisor variable management

## Considerations

1. **State Compatibility**: Supervisor state extends AgentState for seamless integration
2. **Supervisor Chat Messages**: Critical for maintaining conversation context and enabling response mode
3. **Error Handling**: Robust error handling for agent failures and timeouts
4. **Resource Management**: Efficient resource usage when running multiple agents
5. **Security**: Proper isolation between agents and secure A2A communication
6. **Observability**: Logging and monitoring for supervisor operations
7. **Variable Isolation**: Each agent's variables are isolated, with supervisor aggregating as needed
8. **Message Isolation**: Sub-agents maintain their own chat_messages, while supervisor_chat_messages tracks the high-level conversation
