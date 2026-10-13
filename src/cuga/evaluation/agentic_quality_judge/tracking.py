"""Real tool-call-with-result tracking for AgentRunner-driven eval loops.

`cuga evaluate`'s own tool-call extraction (evaluate_cuga.py::parse_test_results)
reads "api_call" ActivityTracker steps -- CugaLite's fast-execution path (the
one actually used whenever the tool count is below
settings.shortlisting_tool_threshold) never emits steps named that way, so
that extraction silently finds nothing, even when the agent's calls
genuinely succeeded. Confirmed directly: a call
that produced a real recorded success still showed up as
tool_call_mismatches: [{"type": "missing", "actual": null}].

AgentState's own `tool_calls` field (populated by ToolCallTracker) is the
reliable alternative -- it carries real name/arguments/result -- but only
accumulates when configurable["track_tool_calls"] is set, which no public
AgentRunner/AgentLoop entry point exposes -- so this monkeypatches AgentLoop
construction to inject it into every graph invocation's config.

extract_tool_calls_with_results() below serves two consumers: the
agentic_quality judge (which only reads "result"), and rebuilding the actual
ToolCall list for tool_call_score's exact-match comparison, which needs
"arguments" too.
"""


def enable_tool_call_tracking():
    from cuga.backend.cuga_graph.utils.agent_loop import AgentLoop

    original_init = AgentLoop.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        original_graph_astream = self.graph.astream

        def astream_with_tracking(input, config=None, **kw):
            if config is not None and "configurable" in config:
                config["configurable"]["track_tool_calls"] = True
            return original_graph_astream(input, config=config, **kw)

        self.graph.astream = astream_with_tracking

    AgentLoop.__init__ = patched_init


def extract_tool_calls_with_results(state):
    """Build [{"tool": name, "arguments": {...}, "result": output, "app_name": ...}, ...]
    from the final AgentState's `tool_calls` field. Each entry there already
    carries the real arguments and result CUGA's sandbox executor captured --
    requires enable_tool_call_tracking() to have been called before the
    AgentRunner/AgentLoop that produced `state` was constructed.

    `tool` is the registry's app-prefixed callable name (e.g.
    "petstore_getpetbyid"), not the bare OpenAPI operationId
    ("getPetById") that test-case `expected_output.tool_calls` uses --
    callers matching against expected tool calls need to strip the
    f"{app_name}_" prefix themselves (`app_name` is included for exactly
    that)."""
    calls = []
    for call in state.tool_calls or []:
        calls.append(
            {
                "tool": call.get("name", ""),
                "arguments": call.get("arguments") or {},
                "result": call.get("result"),
                "app_name": call.get("app_name", ""),
            }
        )
    return calls
