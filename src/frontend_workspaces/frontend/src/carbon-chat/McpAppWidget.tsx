import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppRenderer } from "@mcp-ui/client";
import { Button, InlineNotification, Tag } from "@carbon/react";
import { Application } from "@carbon/icons-react";
import { ContentSkeleton } from "agentic_chat/LoadingState";
import {
  callMcpAppTool,
  fetchMcpAppResource,
  type McpAppActionInfo,
  type McpAppResource,
  sandboxProxyUrl,
  updateMcpAppModelContext,
} from "./mcpApps";
import "./McpAppWidget.css";

export interface McpAppOutcome {
  message?: string;
  cancelled?: boolean;
}

type Phase = "loading" | "pending" | "submitting" | "submitted" | "cancelled" | "error";

const HOST_INFO = { name: "CUGA", version: "1.0.0" };
// What this host does for an app: proxy its server's tools, take its model context and messages.
const HOST_CAPABILITIES = {
  serverTools: {},
  openLinks: {},
  logging: {},
  message: { text: {} },
  updateModelContext: { text: {}, structuredContent: {} },
};

function textOf(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content
    .filter((c: any) => c && c.type === "text" && typeof c.text === "string")
    .map((c: any) => c.text)
    .join("\n")
    .trim();
}

/**
 * Renders an MCP Apps UI resource (MCP Apps / SEP-1865) for a paused tool call.
 * The app runs in a double-iframe sandbox on another origin; every request it
 * makes goes through the CUGA server, scoped to this interaction.
 */
export function McpAppWidget({
  info,
  threadId,
  onFinish,
}: {
  info: McpAppActionInfo;
  threadId: string | null;
  onFinish: (outcome: McpAppOutcome) => Promise<void> | void;
}) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [error, setError] = useState<string | null>(null);
  const [resource, setResource] = useState<McpAppResource | null>(null);
  const [sandboxUrl, setSandboxUrl] = useState<URL | null>(null);
  const finishedRef = useRef(false);
  const onFinishRef = useRef(onFinish);
  onFinishRef.current = onFinish;

  const title = info.tool_title || info.agent_tool || info.tool;

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    setError(null);
    Promise.all([fetchMcpAppResource(info.interaction_id, threadId, controller.signal), sandboxProxyUrl()])
      .then(([res, url]) => {
        if (controller.signal.aborted) return;
        setResource(res);
        setSandboxUrl(url);
        if (res.status === "completed") {
          finishedRef.current = true;
          setPhase("submitted");
        } else {
          setPhase("pending");
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : String(err));
        setPhase("error");
      });
    return () => controller.abort();
  }, [info.interaction_id, threadId]);

  const finish = useCallback((outcome: McpAppOutcome) => {
    if (finishedRef.current) return;
    finishedRef.current = true;
    setPhase(outcome.cancelled ? "cancelled" : "submitting");
    // The stream that resumes the agent runs for the rest of the turn: don't hold the app's request open.
    Promise.resolve(onFinishRef.current(outcome))
      .then(() => setPhase(outcome.cancelled ? "cancelled" : "submitted"))
      .catch((err: unknown) => {
        finishedRef.current = false;
        setError(err instanceof Error ? err.message : String(err));
        setPhase("pending");
      });
  }, []);

  const onCallTool = useCallback(
    async (params: { name: string; arguments?: Record<string, unknown> }) => {
      if (finishedRef.current) {
        return { isError: true, content: [{ type: "text", text: "This form is already complete." }] } as any;
      }
      try {
        return (await callMcpAppTool(info.interaction_id, params.name, params.arguments, threadId)) as any;
      } catch (err) {
        return {
          isError: true,
          content: [{ type: "text", text: err instanceof Error ? err.message : String(err) }],
        } as any;
      }
    },
    [info.interaction_id, threadId],
  );

  const onMessage = useCallback(
    async (params: { role: string; content: unknown }) => {
      finish({ message: textOf(params.content) });
      return {};
    },
    [finish],
  );

  const onFallbackRequest = useCallback(
    async (request: { method: string; params?: any }) => {
      if (request.method === "ui/update-model-context") {
        await updateMcpAppModelContext(
          info.interaction_id,
          { content: request.params?.content, structuredContent: request.params?.structuredContent },
          threadId,
        );
        return {};
      }
      throw new Error(`Unsupported request: ${request.method}`);
    },
    [info.interaction_id, threadId],
  );

  const onOpenLink = useCallback(async ({ url }: { url: string }) => {
    if (/^https?:\/\//i.test(url)) window.open(url, "_blank", "noopener,noreferrer");
    return {};
  }, []);

  const onError = useCallback((err: Error) => {
    console.error("[McpAppWidget]", err);
    setError(err.message);
  }, []);

  const hostContext = useMemo(
    () => ({
      theme: "light" as const,
      displayMode: "inline" as const,
      availableDisplayModes: ["inline" as const],
      locale: navigator.language,
      platform: "web" as const,
    }),
    [],
  );

  const statusTag = {
    loading: null,
    pending: <Tag type="blue" size="sm">Waiting for you</Tag>,
    submitting: <Tag type="cyan" size="sm">Sending to agent…</Tag>,
    submitted: <Tag type="green" size="sm">Submitted</Tag>,
    cancelled: <Tag type="gray" size="sm">Closed</Tag>,
    error: <Tag type="red" size="sm">Unavailable</Tag>,
  }[phase];

  // A form finished before this page loaded is summarized, not re-opened. One finished just
  // now stays on screen: the app shows its own confirmation.
  const showApp = resource && sandboxUrl && phase !== "error" && resource.status !== "completed";

  return (
    <section className="cuga-mcp-app" aria-label={`${title} form`} data-phase={phase}>
      <header className="cuga-mcp-app__header">
        <Application size={16} aria-hidden="true" className="cuga-mcp-app__icon" />
        <span className="cuga-mcp-app__title">{title}</span>
        {statusTag}
        <span className="cuga-mcp-app__source">Interactive tool · {info.server}</span>
      </header>

      {phase === "loading" && <ContentSkeleton label="Loading form" kind="table" rows={4} showHeader={false} />}

      {error && (
        <InlineNotification
          kind="error"
          lowContrast
          hideCloseButton
          title={phase === "error" ? "Could not open the form" : "Form error"}
          subtitle={error}
          className="cuga-mcp-app__notice"
        />
      )}

      {showApp && (
        <div className="cuga-mcp-app__frame">
          <AppRenderer
            toolName={info.tool}
            toolResourceUri={info.resource_uri}
            html={resource.html}
            sandbox={{ url: sandboxUrl, csp: resource.csp as any }}
            toolInput={resource.tool_input}
            toolResult={resource.tool_result as any}
            hostInfo={HOST_INFO}
            hostCapabilities={HOST_CAPABILITIES as any}
            hostContext={hostContext as any}
            onCallTool={onCallTool as any}
            onMessage={onMessage as any}
            onFallbackRequest={onFallbackRequest as any}
            onOpenLink={onOpenLink as any}
            onError={onError}
          />
        </div>
      )}

      {phase === "submitted" && resource?.status === "completed" && (
        <p className="cuga-mcp-app__done">You already submitted this form.</p>
      )}

      {phase === "pending" && (
        <footer className="cuga-mcp-app__footer">
          <Button kind="ghost" size="sm" onClick={() => finish({ cancelled: true })}>
            Close without submitting
          </Button>
        </footer>
      )}
    </section>
  );
}

export default McpAppWidget;
