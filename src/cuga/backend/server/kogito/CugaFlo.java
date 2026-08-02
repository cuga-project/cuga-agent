package org.cuga;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import org.jbpm.workflow.instance.impl.WorkflowProcessInstanceImpl;
import org.kie.kogito.internal.process.runtime.KogitoProcessContext;

/**
 * MCP client for CUGA FLO's control points, called from BPMN script tasks.
 *
 * <p>The Kogito counterpart of the inline Nashorn scripts in the Flowable model. It exists
 * as a class rather than inline script because Kogito's validator rejects Java FQNs inside
 * {@code <bpmn2:script>} — it reads the leading package segment as an undeclared variable.
 * Scripts therefore stay one-liners: {@code CugaFlo.executeTask(kcontext, "Activity_x", "..")}.
 *
 * <p>Every call ships the full process-variable map, so CUGA FLO always sees complete state.
 * The MCP endpoint comes from the {@code cugaMcpUrl} process variable, injected by
 * {@code MCPFlowBridge.register_kogito_engine} — not hardcoded, because the right host
 * differs between {@code mvn quarkus:dev} on the host and a containerised Kogito.
 */
public final class CugaFlo {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    /**
     * LLM reasoning at a control point takes 10-60s; the Flowable model allows 120s too.
     *
     * <p>HTTP/1.1 is pinned deliberately. Java's HttpClient defaults to HTTP/2 and opens
     * with an h2c upgrade, which uvicorn (serving the MCP app) rejects outright — the
     * symptom is a 400 "Invalid HTTP request received." before the JSON is ever read.
     */
    private static final HttpClient HTTP = HttpClient.newBuilder()
            .version(HttpClient.Version.HTTP_1_1)
            .connectTimeout(Duration.ofSeconds(10))
            .build();

    private CugaFlo() {}

    // ─── control points ──────────────────────────────────────────────────

    /**
     * Run a task through CUGA FLO's TaskAgent and write its outputs back.
     *
     * <p>The reply is a serialised FlowState; every key in its {@code process_variables}
     * that the process already declares is copied back, which is how {@code credit_score}
     * reaches the gateway.
     */
    public static void executeTask(KogitoProcessContext kcontext, String taskId, String taskName) {
        ObjectNode args = MAPPER.createObjectNode();
        args.put("task_id", taskId);
        args.set("ctx", context(kcontext, taskId, taskName));

        JsonNode state = asJson(call(kcontext, "execute_task", args));
        JsonNode vars = state.path("process_variables");

        WorkflowProcessInstanceImpl pi = instance(kcontext);
        Map<String, Object> declared = pi.getVariables();
        vars.fieldNames().forEachRemaining(name -> {
            if (declared.containsKey(name)) {
                pi.setVariable(name, coerce(vars.get(name), declared.get(name)));
            }
        });
    }

    /**
     * Ask CUGA FLO's DecisionAgent which flow to take and store the answer.
     *
     * <p>The gateway's outgoing {@code conditionExpression}s are equality checks against
     * {@code outVar}, exactly as in the Flowable model — the variable is the only channel
     * between the agent's decision and the gateway.
     *
     * @param flowsJson JSON array of the gateway's outgoing flows, from the BPMN
     * @param outVar    variable the chosen flow id is written to
     */
    public static void routeGateway(KogitoProcessContext kcontext, String gatewayId,
                                    String gatewayName, String flowsJson, String outVar) {
        try {
            ObjectNode ctx = context(kcontext, gatewayId, gatewayName);
            ctx.set("available_flows", MAPPER.readTree(flowsJson));

            ObjectNode args = MAPPER.createObjectNode();
            args.put("gateway_id", gatewayId);
            args.set("ctx", ctx);

            // route_gateway returns the bare flow id, not a JSON document.
            String flowId = call(kcontext, "route_gateway", args).trim().replaceAll("^\"|\"$", "");
            instance(kcontext).setVariable(outVar, flowId);
        } catch (Exception e) {
            throw new IllegalStateException("route_gateway failed for " + gatewayId, e);
        }
    }

    /**
     * Evaluate a hook and return where execution should go next.
     *
     * @return the BPMN element id to jump to, or {@code ""} to continue normally — feed it
     *         straight to {@link FlowRedirect#to}, which treats blank as a no-op.
     */
    public static String evaluateHook(KogitoProcessContext kcontext, String hookId,
                                      String hookName, String terminateTarget) {
        ObjectNode args = MAPPER.createObjectNode();
        args.put("hook_id", hookId);
        args.set("ctx", context(kcontext, hookId, hookName));

        JsonNode result = asJson(call(kcontext, "evaluate_hook", args));
        String action = result.path("action").asText("continue");

        WorkflowProcessInstanceImpl pi = instance(kcontext);
        Map<String, Object> declared = pi.getVariables();
        JsonNode updates = result.path("state_updates");
        if (updates.isObject()) {
            updates.fieldNames().forEachRemaining(name -> {
                if (declared.containsKey(name)) {
                    pi.setVariable(name, coerce(updates.get(name), declared.get(name)));
                }
            });
        }

        switch (action) {
            case "skip_to":
                return result.path("skip_to_node").asText("");
            case "terminate":
                pi.setVariable("_haltReason",
                        result.path("message").asText("Hook terminated the process"));
                return terminateTarget;
            default:
                return "";
        }
    }

    /** Hand the terminal state back to FlowAgent, which is blocked awaiting it. */
    public static void completeProcess(KogitoProcessContext kcontext) {
        WorkflowProcessInstanceImpl pi = instance(kcontext);
        Map<String, Object> vars = pi.getVariables();

        ObjectNode state = MAPPER.createObjectNode();
        state.set("process_variables", MAPPER.valueToTree(vars));
        String halt = String.valueOf(vars.getOrDefault("_haltReason", ""));
        state.put("is_halted", !halt.isEmpty());
        state.put("halt_reason", halt);

        ObjectNode args = MAPPER.createObjectNode();
        args.put("process_key", String.valueOf(vars.getOrDefault("cugaProcessKey", "")));
        args.set("state", state);

        call(kcontext, "complete_process", args);
    }

    // ─── plumbing ────────────────────────────────────────────────────────

    /** Build the ControlPointFlowKnowledge payload CUGA FLO expects on every call. */
    private static ObjectNode context(KogitoProcessContext kcontext, String elementId, String elementName) {
        WorkflowProcessInstanceImpl pi = instance(kcontext);

        ObjectNode currentState = MAPPER.createObjectNode();
        currentState.set("process_variables", MAPPER.valueToTree(pi.getVariables()));

        ObjectNode ctx = MAPPER.createObjectNode();
        ctx.put("process_instance_id", pi.getStringId());
        ctx.put("element_id", elementId);
        ctx.put("element_name", elementName);
        ctx.set("current_state", currentState);
        ctx.set("execution_history", MAPPER.createArrayNode());
        ctx.set("process_model_summary", MAPPER.createObjectNode());
        return ctx;
    }

    /**
     * Invoke an MCP tool and return the text payload of its first content block.
     *
     * <p>FastMCP's stateless HTTP app answers with SSE, so the JSON-RPC envelope arrives on
     * a {@code data:} line; a plain JSON body is accepted too in case that changes.
     */
    private static String call(KogitoProcessContext kcontext, String tool, ObjectNode arguments) {
        Object url = instance(kcontext).getVariables().get("cugaMcpUrl");
        if (url == null || String.valueOf(url).isBlank()) {
            throw new IllegalStateException("cugaMcpUrl process variable is not set");
        }

        ObjectNode params = MAPPER.createObjectNode();
        params.put("name", tool);
        params.set("arguments", arguments);

        ObjectNode body = MAPPER.createObjectNode();
        body.put("jsonrpc", "2.0");
        body.put("method", "tools/call");
        body.set("params", params);
        body.put("id", 1);

        try {
            HttpRequest request = HttpRequest.newBuilder(URI.create(String.valueOf(url)))
                    .header("Content-Type", "application/json")
                    .header("Accept", "application/json, text/event-stream")
                    .timeout(Duration.ofSeconds(120))
                    .POST(HttpRequest.BodyPublishers.ofString(MAPPER.writeValueAsString(body)))
                    .build();

            HttpResponse<String> response = HTTP.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() != 200) {
                // The body carries the real reason (bad JSON-RPC, wrong tool name, an
                // HTTP-level rejection); the status code alone is never enough to debug.
                throw new IllegalStateException(
                        tool + " -> HTTP " + response.statusCode() + ": " + response.body());
            }

            JsonNode envelope = MAPPER.readTree(unwrapSse(response.body()));
            if (!envelope.has("result")) {
                throw new IllegalStateException(tool + " returned error: " + envelope.path("error"));
            }
            return envelope.path("result").path("content").path(0).path("text").asText();
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            throw new IllegalStateException("MCP call " + tool + " failed", e);
        }
    }

    /** Pull the JSON out of an SSE body; pass a plain JSON body through untouched. */
    private static String unwrapSse(String body) {
        for (String line : body.split("\n")) {
            if (line.startsWith("data:")) {
                return line.substring(5).trim();
            }
        }
        return body.trim();
    }

    private static JsonNode asJson(String text) {
        try {
            return MAPPER.readTree(text);
        } catch (Exception e) {
            throw new IllegalStateException("expected JSON from CUGA FLO, got: " + text, e);
        }
    }

    /**
     * Convert an incoming JSON value to the type the declared variable already holds.
     *
     * <p>Kogito's process model is strongly typed, so assigning a Double to a variable
     * declared Float fails at runtime. The current value is the only type evidence
     * available here.
     */
    private static Object coerce(JsonNode value, Object current) {
        if (value == null || value.isNull()) {
            return null;
        }
        if (current instanceof Float) {
            return (float) value.asDouble();
        }
        if (current instanceof Double) {
            return value.asDouble();
        }
        if (current instanceof Integer) {
            return value.asInt();
        }
        if (current instanceof Long) {
            return value.asLong();
        }
        if (current instanceof Boolean) {
            return value.asBoolean();
        }
        return value.isValueNode() ? value.asText() : value.toString();
    }

    private static WorkflowProcessInstanceImpl instance(KogitoProcessContext kcontext) {
        return (WorkflowProcessInstanceImpl) kcontext.getProcessInstance();
    }
}
