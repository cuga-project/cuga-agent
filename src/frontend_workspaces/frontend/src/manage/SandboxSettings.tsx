import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button, Checkbox, InlineLoading, InlineNotification, VStack } from "@carbon/react";
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
 * Code sandbox (execd) on/off. Applied to the running server immediately and
 * stored on its own — not part of the agent config draft/publish cycle.
 *
 * The connection comes from the mounted sandbox secret, so switching on is all
 * it takes; the server checks the sandbox answers as soon as it is on. Shown
 * only when such a connection exists (see ManagePage).
 */
export function SandboxSettings({ onError, onSaved }: Props) {
  const [status, setStatus] = useState<SandboxSettingsStatus | null>(null);
  const [checked, setChecked] = useState(false);
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

  const toggle = async (enable: boolean) => {
    setChecked(enable);
    const s = await call(
      () => api.putSandboxSettings({ enabled: enable }),
      enable ? "Sandbox enabled" : "Sandbox disabled"
    );
    // Refused or failed: show the state the server still has.
    if (!s) setChecked(status.enabled);
  };

  const on = status.enabled && checked;
  // What is wrong, in words. Like the LLM settings, success needs no detail and
  // the URL / HTTP status belong in the server log, not here.
  const problem = !on ? null : status.error ?? status.probe_error ?? null;
  const ok = status.reachable === true && !problem;

  return (
    <VStack gap={5}>
      <Checkbox
        id="sandbox-enabled"
        labelText="Sandbox"
        helperText={
          (status.required_by_config
            ? 'Required by the deployment (sandbox_mode = "execd"). '
            : "Run generated code in a remote execd sandbox. Applies to this server process; " +
              "other replicas pick it up when they restart. ") +
          "Applies to all agents and users. Conversations are isolated by the sandbox, not by CUGA."
        }
        checked={checked}
        disabled={busy || status.required_by_config}
        onChange={(_e: React.ChangeEvent<HTMLInputElement>, { checked: on }: { checked: boolean }) => toggle(on)}
      />

      {/* Kept while a re-check runs, so the box does not vanish and come back. */}
      {problem && (
        <InlineNotification kind="error" lowContrast hideCloseButton title="Sandbox unavailable" subtitle={problem} />
      )}

      {/* One fixed row: the status changes in place instead of boxes appearing around it. */}
      {checked && (
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap", minHeight: "2rem" }}>
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
