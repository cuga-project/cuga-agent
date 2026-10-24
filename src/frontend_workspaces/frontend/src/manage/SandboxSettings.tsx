import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button, Checkbox, InlineLoading, InlineNotification, TextInput, VStack } from "@carbon/react";
import * as api from "../api";
import type { SandboxSettingsStatus } from "../api";

interface Props {
  onError: (title: string, message: string) => void;
  onSaved?: (message: string) => void;
}

async function readError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
  } catch {
    return `HTTP ${res.status}`;
  }
}

/**
 * Code sandbox (execd) settings. Applied to the running server immediately and
 * stored on their own — not part of the agent config draft/publish cycle.
 *
 * A mounted secret supplies the connection by itself, so switching on is all it
 * takes; without one the URL and API key are asked for. Either way the server
 * checks the sandbox answers as soon as it is on.
 */
export function SandboxSettings({ onError, onSaved }: Props) {
  const [status, setStatus] = useState<SandboxSettingsStatus | null>(null);
  const [checked, setChecked] = useState(false);
  const [url, setUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [busy, setBusy] = useState(false);
  // The parent passes fresh callbacks on every render (each toast re-renders it).
  // Read them through refs so `call` stays stable and the mount effect runs once,
  // instead of re-fetching and re-testing the sandbox after every toast.
  const onErrorRef = useRef(onError);
  const onSavedRef = useRef(onSaved);
  onErrorRef.current = onError;
  onSavedRef.current = onSaved;

  const applyStatus = useCallback((s: SandboxSettingsStatus) => {
    setStatus(s);
    setChecked(s.enabled);
    setUrl(s.manual?.url || "");
    setApiKey("");
  }, []);

  const call = useCallback(
    async (fn: () => Promise<Response>, okMessage?: string): Promise<SandboxSettingsStatus | null> => {
      setBusy(true);
      try {
        const res = await fn();
        if (!res.ok) {
          onErrorRef.current("Code sandbox", await readError(res));
          return null;
        }
        const s: SandboxSettingsStatus = await res.json();
        applyStatus(s);
        if (okMessage) onSavedRef.current?.(okMessage);
        return s;
      } catch (e) {
        onErrorRef.current("Code sandbox", String(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [applyStatus]
  );

  useEffect(() => {
    (async () => {
      const s = await call(() => api.getSandboxSettings());
      // Already on: check it still answers rather than showing a stale state.
      if (s?.enabled && !s.error) await call(() => api.refreshSandboxSettings());
    })();
  }, [call]);

  if (!status) return <InlineLoading description="Loading sandbox settings…" />;

  const usesSecret = status.connection === "secret"; // pragma: allowlist secret
  const needsUrl = checked && !usesSecret;

  const toggle = (enable: boolean) => {
    setChecked(enable);
    if (enable && usesSecret) call(() => api.putSandboxSettings({ enabled: true }), "Sandbox enabled");
    if (!enable && status.enabled) call(() => api.putSandboxSettings({ enabled: false }), "Sandbox disabled");
  };

  const connect = () =>
    call(
      () => api.putSandboxSettings({ enabled: true, url: url.trim(), api_key: apiKey || undefined }), // pragma: allowlist secret
      "Sandbox settings applied"
    );

  const on = status.enabled && checked;
  const ok = status.reachable === true && (status.status_code ?? 0) < 400;
  // What is wrong, in words. Like the LLM settings, success needs no detail and
  // the URL / HTTP status belong in the server log, not here.
  const problem = !on
    ? null
    : status.error ??
      (status.reachable === false
        ? status.probe_error || "The sandbox does not answer"
        : status.reachable && !ok
          ? status.auth_error || `The sandbox answered HTTP ${status.status_code}`
          : null);

  return (
    <VStack gap={5}>
      <Checkbox
        id="sandbox-enabled"
        labelText="Sandbox"
        helperText={
          status.required_by_config
            ? 'Required by the deployment (sandbox_mode = "execd")'
            : "Run generated code in a remote execd sandbox"
        }
        checked={checked}
        disabled={busy || status.required_by_config}
        onChange={(_e: React.ChangeEvent<HTMLInputElement>, { checked: on }: { checked: boolean }) => toggle(on)}
      />

      {needsUrl && (
        <VStack gap={4}>
          <InlineNotification
            kind="info"
            lowContrast
            hideCloseButton
            title="No sandbox secret mounted"
            subtitle="Enter the URL and API key."
          />
          <TextInput
            id="sandbox-url"
            labelText="URL"
            placeholder="http://"
            value={url}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setUrl(e.target.value)}
          />
          <TextInput
            id="sandbox-api-key"
            type="password"
            labelText="API key"
            helperText={status.manual?.api_key_set ? "Saved — leave empty to keep it" : undefined}
            value={apiKey}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => setApiKey(e.target.value)}
            autoComplete="off"
          />
        </VStack>
      )}

      {/* Kept while a re-check runs, so the box does not vanish and come back. */}
      {problem && (
        <InlineNotification kind="error" lowContrast hideCloseButton title="Sandbox unavailable" subtitle={problem} />
      )}

      {/* One fixed row: the status changes in place instead of boxes appearing around it. */}
      {checked && (
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", minHeight: "2rem" }}>
          {needsUrl && (
            <Button size="sm" disabled={busy || !url.trim()} onClick={connect}>
              Connect
            </Button>
          )}
          {on && (
            <Button size="sm" kind="tertiary" disabled={busy} onClick={() => call(() => api.refreshSandboxSettings())}>
              Test again
            </Button>
          )}
          {busy ? (
            <InlineLoading description="Checking…" />
          ) : (
            on && ok && <InlineLoading status="finished" description="Connected" />
          )}
        </div>
      )}
    </VStack>
  );
}
