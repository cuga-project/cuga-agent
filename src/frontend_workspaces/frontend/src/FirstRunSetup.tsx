import React, { useEffect, useState } from "react";
import { Button, InlineNotification, Select, SelectItem, TextInput } from "@carbon/react";
import * as api from "./api";

export function FirstRunSetup() {
  const [visible, setVisible] = useState(false);
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [credential, setCredential] = useState("");
  const [storedCredentialRef, setStoredCredentialRef] = useState("");
  const [busy, setBusy] = useState(false);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api.apiFetch("/api/manage/setup/status").then(r => r.json()).then(s => {
      setVisible(s.enabled && !s.configured);
    }).catch(() => {});
  }, []);
  if (!visible) return null;
  const testConnection = async () => {
    setBusy(true); setError(""); setVerified(false);
    try {
      let keyRef = storedCredentialRef;
      if (credential) {
        const id = `setup-${crypto.randomUUID()}`;
        const secret = await api.createSecret(id, credential, "Local provider credential", undefined, "cuga-default"); // pragma: allowlist secret
        if (!secret.ok) throw new Error("Could not save the credential locally.");
        keyRef = `db://${id}`; // pragma: allowlist secret (Secret reference, not a credential.)
        setStoredCredentialRef(keyRef);
        setCredential("");
      }
      const config = {
        provider: provider === "compatible" || provider === "ollama" ? "openai" : provider,
        model: model.trim(),
        base_url: provider === "ollama" ? `${endpoint.trim().replace(/\/$/, "").replace(/\/v1$/, "")}/v1` : provider === "compatible" ? endpoint.trim() : "",
        url: null,
        api_key: provider === "ollama" ? "ollama" : keyRef || null, // pragma: allowlist secret (Local Ollama placeholder.)
        auth_type: "api_key",
        auth_header_name: "Authorization",
        disable_ssl: false,
      };
      const saved = await api.patchManageConfigDraftLlm(config, "cuga-default");
      if (!saved.ok) throw new Error("Could not save provider settings.");
      const tested = await api.apiFetch("/api/manage/setup/validate", { method: "POST" });
      const result = await tested.json();
      if (!tested.ok) throw new Error(result.detail || "Connection test failed.");
      setVerified(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connection test failed.");
    } finally { setBusy(false); }
  };
  return <section aria-label="First-run setup" style={{ padding: "1.5rem", marginBottom: "2rem", background: "var(--cds-layer-01)", borderTop: "3px solid #0f62fe", maxWidth: "48rem" }}>
    <h2 style={{ marginBottom: ".5rem" }}>Set up your first agent</h2>
    <p style={{ marginBottom: "1.5rem" }}>1. Choose your inference provider. 2. Test the connection. 3. Try a task.</p>
    <p style={{ marginBottom: "1rem", color: "var(--cds-text-secondary)" }}>Credentials are stored in your local encrypted secret store. The connection test sends a short request to your selected provider.</p>
    {verified ? <>
      <InlineNotification kind="success" title="Connection verified" subtitle="Your draft is ready. Open Configure & try it out, then ask: What can you help me automate?" hideCloseButton />
      <Button href="/manage/cuga-default?first-task=1" style={{ marginTop: "1rem" }}>Try your first task</Button>
    </> : <>
      <Select id="setup-provider" labelText="Provider" value={provider} disabled={busy} onChange={e => { setProvider(e.target.value); setEndpoint(""); setCredential(""); setStoredCredentialRef(""); }}>
        <SelectItem value="openai" text="OpenAI" /><SelectItem value="openrouter" text="OpenRouter" />
        <SelectItem value="groq" text="Groq" /><SelectItem value="ollama" text="Ollama (local)" />
        <SelectItem value="compatible" text="OpenAI-compatible private endpoint" />
      </Select>
      <TextInput id="setup-model" labelText="Model identifier" helperText="Use a model available to your provider or local server." value={model} disabled={busy} onChange={e => setModel(e.target.value)} style={{ marginTop: "1rem" }} />
      {(provider === "compatible" || provider === "ollama") && <TextInput id="setup-endpoint" labelText="Endpoint URL" placeholder={provider === "ollama" ? "http://localhost:11434" : "https://your-endpoint/v1"} value={endpoint} disabled={busy} onChange={e => setEndpoint(e.target.value)} style={{ marginTop: "1rem" }} />}
      {provider !== "ollama" && <TextInput id="setup-credential" type="password" autoComplete="off" labelText="API key" helperText="Entered locally; never added to your agent configuration as plain text." value={credential} disabled={busy} onChange={e => setCredential(e.target.value)} style={{ marginTop: "1rem" }} />}
      {error && <InlineNotification kind="error" title="Check your connection" subtitle={error} hideCloseButton style={{ marginTop: "1rem" }} />}
      <Button onClick={testConnection} disabled={busy || !model.trim() || (provider !== "ollama" && !credential && !storedCredentialRef) || ((provider === "compatible" || provider === "ollama") && !endpoint.trim())} style={{ marginTop: "1rem" }}>{busy ? "Testing connection…" : "Save and test connection"}</Button>
    </>}
  </section>;
}
