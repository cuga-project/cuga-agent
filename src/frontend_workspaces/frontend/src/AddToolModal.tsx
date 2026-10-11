import React, { useState, useEffect } from "react";
import {
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
  TextInput,
  TextArea,
  FormGroup,
  Select,
  SelectItem,
  Tile,
  ClickableTile,
  Checkbox,
  InlineNotification,
} from "@carbon/react";
import { Template, Folder } from "@carbon/icons-react";
import type { ToolEntry, ToolAuth, AuthType, GatewayServer } from "./types/tools";
import { AUTH_TYPE_OPTIONS, parseGatewayInstanceUrl } from "./types/tools";
import * as api from "./api";
import "./AddToolModal.css";

interface AddToolModalProps {
  onClose: () => void;
  onSave: (tool: ToolEntry) => void;
  initial?: ToolEntry | null;
  agentId?: string;
}

const emptyAuth: ToolAuth = { type: "none" };

type McpConnectionMode = "url" | "url-http" | "command" | "gateway";

const GATEWAY_AUTH_TYPE: AuthType = "account_iam_apikey";
const SECRET_REF_PREFIXES = ["db://", "vault://", "aws://"];

// Pre-configured tool templates
interface ToolTemplate {
  id: string;
  name: string;
  description: string;
  icon: React.ComponentType<any>;
  config: Partial<ToolEntry> & { mcpMode?: McpConnectionMode; argsText?: string };
}

const TOOL_TEMPLATES: ToolTemplate[] = [
  {
    id: "filesystem",
    name: "Filesystem",
    description: "Read and write files in a specified directory",
    icon: Folder,
    config: {
      name: "filesystem",
      type: "mcp",
      mcpMode: "command",
      command: "npx",
      argsText: "-y\n@modelcontextprotocol/server-filesystem\n./cuga_workspace",
      description: "Filesystem access for reading and writing files",
      transport: "stdio",
    },
  },
  {
    id: "drawio",
    name: "Drawio",
    description: "Create and manipulate diagrams using Draw.io",
    icon: Template,
    config: {
      name: "drawio",
      type: "mcp",
      mcpMode: "command",
      command: "npx",
      argsText: "-y\n@next-ai-drawio/mcp-server@latest",
      description: "Drawio diagram creation and manipulation server",
      transport: "stdio",
    },
  },
  {
    id: "browser_mcp",
    name: "Browser_MCP",
    description: "Browser automation and web interaction capabilities",
    icon: Template,
    config: {
      name: "browser_mcp",
      type: "mcp",
      mcpMode: "command",
      command: "npx",
      argsText: "-y\n@agent-infra/mcp-server-browser@latest",
      description: "Browser automation and web interaction server",
      transport: "stdio",
    },
  },
];

function initFromTool(initial: ToolEntry | null | undefined) {
  const auth = initial?.auth ?? emptyAuth;
  const val = auth.value ?? "";
  const hasCmd = !!(initial?.command?.trim());
  const transport = initial?.transport ?? (initial?.url ? "sse" : "stdio");
  return {
    name: initial?.name ?? "",
    type: (initial?.type ?? "mcp") as "mcp" | "openapi",
    mcpMode: (initial?.gateway ? "gateway" : hasCmd ? "command" : transport === "http" ? "url-http" : "url") as McpConnectionMode,
    url: initial?.url ?? "",
    command: initial?.command ?? "",
    argsText: (initial?.args ?? []).join("\n"),
    description: initial?.description ?? "",
    authType: (!auth.type || auth.type === "none" ? "none" : auth.type) as AuthType,
    authKey: auth.key ?? "",
    authValue: val,
    useSavedSecret: typeof val === "string" && SECRET_REF_PREFIXES.some((p) => val.startsWith(p)),
  };
}

export function AddToolModal({ onClose, onSave, initial, agentId }: AddToolModalProps) {
  const init = initFromTool(initial);
  const [name, setName] = useState(init.name);
  const [type, setType] = useState<"mcp" | "openapi">(init.type);
  const [mcpMode, setMcpMode] = useState<McpConnectionMode>(init.mcpMode);
  const [url, setUrl] = useState(init.url);
  const [command, setCommand] = useState(init.command);
  const [argsText, setArgsText] = useState(init.argsText);
  const [description, setDescription] = useState(init.description);
  const [envText, setEnvText] = useState(
    Object.entries(initial?.env ?? {}).map(([k, v]) => `${k}=${v}`).join("\n")
  );
  const [authType, setAuthType] = useState<AuthType>(init.authType);
  const [authKey, setAuthKey] = useState(init.authKey);
  const [authValue, setAuthValue] = useState(init.authValue);
  const [useSavedSecret, setUseSavedSecret] = useState(init.useSavedSecret);
  const [saveAsNewSecret, setSaveAsNewSecret] = useState(false);
  const [saveAsNewSecretKey, setSaveAsNewSecretKey] = useState("");
  const [secretsList, setSecretsList] = useState<{ id: string; description?: string; ref: string }[]>([]);
  const [inlineCreateOpen, setInlineCreateOpen] = useState(false);
  const [inlineCreateValue, setInlineCreateValue] = useState("");
  const [inlineCreateKey, setInlineCreateKey] = useState("");
  const [showTemplates, setShowTemplates] = useState(!initial);
  // MCP gateway instance: the instance's servers and tools (after Connect) and the selection.
  const [gatewayServers, setGatewayServers] = useState<GatewayServer[] | null>(null);
  const [gatewaySelected, setGatewaySelected] = useState<Set<string>>(
    () => new Set(initial?.gateway ? initial.include ?? [] : [])
  );
  const [gatewayConnecting, setGatewayConnecting] = useState(false);
  const [gatewayError, setGatewayError] = useState("");
  const [submitError, setSubmitError] = useState("");

  useEffect(() => {
    Promise.all([api.getSecrets(agentId), api.getSecretsConfig()])
      .then(async ([secretsRes, configRes]) => {
        let mode = "local";
        if (configRes.ok) {
          const cfg = await configRes.json();
          mode = cfg.mode || "local";
        }
        if (secretsRes.ok) {
          const data = await secretsRes.json();
          const raw: { id: string; description?: string; source?: string }[] = data.secrets || data.overrides || [];
          setSecretsList(raw.map((s) => ({
            id: s.id,
            description: s.description,
            ref: s.source === "vault" || mode === "vault"
              ? `vault://secret/${s.id}#value`
              : s.source === "env"
                ? s.id
                : `db://${s.id}`,
          })));
        }
      })
      .catch(() => {});
  }, [agentId]);

  const applyTemplate = (template: ToolTemplate) => {
    const config = template.config;
    setName(config.name || "");
    setType(config.type || "mcp");
    setMcpMode(config.mcpMode || "url");
    setUrl(config.url || "");
    setCommand(config.command || "");
    setArgsText(config.argsText || (config.args || []).join("\n"));
    setDescription(config.description || "");
    const auth = config.auth ?? emptyAuth;
    setAuthType(auth.type === "none" || !auth.type ? "none" : auth.type);
    setAuthKey(auth.key ?? "");
    setAuthValue(auth.value ?? "");
    setEnvText(Object.entries(config.env ?? {}).map(([k, v]) => `${k}=${v}`).join("\n"));
    setShowTemplates(false);
  };

  const isGateway = type === "mcp" && mcpMode === "gateway";
  const effectiveAuthType: AuthType = isGateway ? GATEWAY_AUTH_TYPE : authType;
  const authOption = AUTH_TYPE_OPTIONS.find((o) => o.value === authType);
  const needsKey = !isGateway && (authOption?.needsKey ?? false);
  const gatewayInstanceId = isGateway ? parseGatewayInstanceUrl(url) : null;
  // A key typed now, else the saved secret ref (also the one stored on the edited entry).
  const gatewayKeyRef = (() => {
    const v = authValue.trim();
    if (v && SECRET_REF_PREFIXES.some((p) => v.startsWith(p))) return v;
    const prev = initial?.gateway ? initial.auth?.value ?? "" : "";
    return !v && SECRET_REF_PREFIXES.some((p) => prev.startsWith(p)) ? prev : "";
  })();
  const gatewayPlainKey = (() => {
    const v = authValue.trim();
    return v && !SECRET_REF_PREFIXES.some((p) => v.startsWith(p)) ? v : "";
  })();

  const connectGateway = async () => {
    setGatewayError("");
    setGatewayConnecting(true);
    try {
      const res = await api.discoverGateway(
        url.trim(),
        gatewayPlainKey ? { apiKey: gatewayPlainKey } : { apiKeyRef: gatewayKeyRef }, // pragma: allowlist secret
        agentId
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setGatewayServers(null);
        setGatewayError(typeof data.detail === "string" ? data.detail : `Connect failed (${res.status})`);
        return;
      }
      const servers: GatewayServer[] = data.servers ?? [];
      setGatewayServers(servers);
      // Keep only selected tools that still exist; new tools stay unselected.
      const known = new Set(servers.flatMap((sv) => sv.tools.map((t) => t.name)));
      setGatewaySelected((prev) => new Set([...prev].filter((n) => known.has(n))));
    } catch (err) {
      setGatewayError(err instanceof Error ? err.message : "Connect failed");
    } finally {
      setGatewayConnecting(false);
    }
  };

  const toggleGatewayTools = (names: string[], checked: boolean) => {
    setGatewaySelected((prev) => {
      const next = new Set(prev);
      names.forEach((n) => (checked ? next.add(n) : next.delete(n)));
      return next;
    });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError("");
    if (isGateway) {
      // The API key is stored only as a secret; a typed key becomes a new secret first.
      let ref = gatewayKeyRef;
      if (gatewayPlainKey) {
        const baseSlug = saveAsNewSecretKey.trim()
          ? saveAsNewSecretKey.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-")
          : `${name.trim() || "gateway"}-apikey-${Date.now()}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");
        try {
          const res = await api.createSecret(baseSlug, gatewayPlainKey, `MCP gateway API key for ${name.trim()}`, undefined, agentId);
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const data = await res.json().catch(() => ({}));
          ref = data.ref || `db://${baseSlug}`;
        } catch (err) {
          setSubmitError(`The API key could not be saved as a secret (${err instanceof Error ? err.message : "error"}). Nothing was saved.`);
          return;
        }
      }
      onSave({
        name: name.trim(),
        type: "mcp",
        url: url.trim(),
        transport: "http",
        description: description.trim() || undefined,
        gateway: { instance_url: url.trim(), run_as: "service" },
        auth: { type: GATEWAY_AUTH_TYPE, value: ref },
        include: [...gatewaySelected].sort(),
      });
      onClose();
      return;
    }
    const isCommandMcp = type === "mcp" && mcpMode === "command";
    const args = argsText.split("\n").map((s) => s.trim()).filter(Boolean);
    const tool: ToolEntry = {
      name: name.trim(),
      type,
      url: isCommandMcp ? undefined : (url.trim() || undefined),
      description: description.trim() || undefined,
    };
    if (isCommandMcp) {
      tool.command = command.trim();
      tool.args = args.length ? args : undefined;
      tool.transport = "stdio";
      const envEntries = envText
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => {
          const idx = l.indexOf("=");
          return idx > 0 ? [l.slice(0, idx).trim(), l.slice(idx + 1).trim()] : null;
        })
        .filter((e): e is [string, string] => e !== null && e[0].length > 0);
      if (envEntries.length > 0) {
        tool.env = Object.fromEntries(envEntries);
      }
    } else if (type === "mcp" && url.trim()) {
      tool.transport = mcpMode === "url-http" ? "http" : "sse";
    }
    if (authType !== "none") {
      let authValueFinal = authValue.trim();

      if (saveAsNewSecret && authValueFinal) {
        const baseSlug = saveAsNewSecretKey.trim()
          ? saveAsNewSecretKey.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-")
          : `${name.trim() || "tool"}-${effectiveAuthType}-${Date.now()}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");
        const slug = baseSlug;
        try {
          const res = await api.createSecret(slug, authValueFinal, `Auth for ${name.trim() || "tool"}`, undefined, agentId);
          if (res.ok) {
            const data = await res.json();
            authValueFinal = data.ref || `db://${slug}`;
          }
        } catch (_) {}
      }

      if (authValueFinal) {
        tool.auth = {
          type: authType,
          ...(needsKey && authKey.trim() && { key: authKey.trim() }),
          value: authValueFinal,
        };
      } else if (initial?.auth && initial.auth.type !== "none") {
        tool.auth = {
          ...initial.auth,
          type: authType,
          ...(needsKey && authKey.trim() && { key: authKey.trim() }),
        };
      }
    }
    onSave(tool);
    onClose();
  };

  const NAME_RE = /^[a-z][a-z0-9_]*$/;
  const nameError = name.trim() === ""
    ? "Name is required"
    : !NAME_RE.test(name.trim())
      ? "Use lowercase letters, digits, and underscores only (e.g. my_tool)"
      : "";

  const isCommandMcp = type === "mcp" && mcpMode === "command";

  // Validate env lines only in command/stdio mode (env UI is hidden otherwise)
  const envLines = isCommandMcp ? envText.split("\n").map((l) => l.trim()).filter(Boolean) : [];
  const badEnvLines = envLines.filter((l) => {
    const idx = l.indexOf("=");
    return idx <= 0 || l.slice(0, idx).trim().length === 0;
  });
  const envError = badEnvLines.length > 0
    ? `Invalid env line${badEnvLines.length > 1 ? "s" : ""}: ${badEnvLines.map((l) => `"${l}"`).join(", ")}. Use KEY=VALUE format.`
    : "";

  const gatewayValid = !!gatewayInstanceId && !!(gatewayPlainKey || gatewayKeyRef) && gatewaySelected.size > 0;
  const valid = !nameError && !envError && description.trim().length > 0 && (
    type === "openapi"
      ? url.trim().length > 0
      : isGateway
        ? gatewayValid
        : isCommandMcp
          ? command.trim().length > 0
          : url.trim().length > 0
  );

  const secretFields = (
    <>
      <Checkbox
        id="tool-use-saved-secret"
        labelText="Use saved secret"
        checked={useSavedSecret}
        onChange={(_e, { checked }) => {
          setUseSavedSecret(!!checked);
          setInlineCreateOpen(false);
        }}
      />
      {useSavedSecret ? (
        <>
          <Select
            id="tool-auth-secret"
            labelText="Secret"
            value={authValue}
            onChange={(e) => setAuthValue(e.target.value)}
          >
            <SelectItem value="" text="Select a secret" />
            {secretsList.map((s) => (
              <SelectItem
                key={s.id}
                value={s.ref}
                text={s.description ? `${s.id} — ${s.description}` : s.id}
              />
            ))}
          </Select>
          <Button
            kind="ghost"
            size="sm"
            style={{ marginTop: "0.5rem" }}
            onClick={() => setInlineCreateOpen((v) => !v)}
          >
            {inlineCreateOpen ? "Cancel" : "Create new secret"}
          </Button>
          {inlineCreateOpen && (
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem", marginTop: "0.5rem" }}>
              <TextInput
                id="tool-inline-secret-key"
                type="text"
                labelText="Key name"
                value={inlineCreateKey}
                onChange={(e) => setInlineCreateKey(e.target.value)}
                placeholder="e.g. my-tool-api-key"
                helperText="Optional; leave empty to auto-generate"
              />
              <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "flex-end" }}>
                <TextInput
                  id="tool-inline-secret-value"
                  type="password"
                  labelText="New secret value"
                  value={inlineCreateValue}
                  onChange={(e) => setInlineCreateValue(e.target.value)}
                  placeholder="Secret value"
                  autoComplete="off"
                />
                <Button
                  size="sm"
                  style={{ marginTop: "auto" }}
                  disabled={!inlineCreateValue.trim()}
                  onClick={async () => {
                    const baseSlug = inlineCreateKey.trim()
                      ? inlineCreateKey.trim().toLowerCase().replace(/\s+/g, "-").replace(/[^a-z0-9-]/g, "-")
                      : `${name.trim() || "tool"}-${effectiveAuthType}-${Date.now()}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");
                    const slug = baseSlug || `${name.trim() || "tool"}-${effectiveAuthType}-${Date.now()}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");
                    const res = await api.createSecret(slug, inlineCreateValue.trim(), `Auth for ${name.trim() || "tool"}`, undefined, agentId);
                    if (res.ok) {
                      setAuthValue(`db://${slug}`);
                      setInlineCreateOpen(false);
                      setInlineCreateKey("");
                      Promise.all([api.getSecrets(agentId), api.getSecretsConfig()])
                        .then(async ([secretsRes, configRes]) => {
                          let mode = "local";
                          if (configRes.ok) {
                            const cfg = await configRes.json();
                            mode = cfg.mode || "local";
                          }
                          if (secretsRes.ok) {
                            const data = await secretsRes.json();
                            const raw: { id: string; description?: string; source?: string }[] = data.secrets || data.overrides || [];
                            setSecretsList(raw.map((s) => ({
                              id: s.id,
                              description: s.description,
                              ref: s.source === "vault" || mode === "vault"
                                ? `vault://secret/${s.id}#value`
                                : s.source === "env"
                                  ? s.id
                                  : `db://${s.id}`,
                            })));
                          }
                        })
                        .catch(() => {});
                    }
                  }}
                >
                  Save
                </Button>
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <TextInput
            id="tool-auth-value"
            type="password"
            labelText="Secret / token / value"
            value={authValue}
            onChange={(e) => setAuthValue(e.target.value)}
            placeholder="Leave empty to keep existing"
            autoComplete="off"
          />
          {!isGateway && (
            <Checkbox
              id="tool-save-as-secret"
              labelText="Save as new secret"
              checked={saveAsNewSecret}
              onChange={(_e, { checked }) => setSaveAsNewSecret(!!checked)}
            />
          )}
          {(saveAsNewSecret || (isGateway && !!gatewayPlainKey)) && (
            <TextInput
              id="tool-save-as-secret-key"
              type="text"
              labelText="Key name"
              value={saveAsNewSecretKey}
              onChange={(e) => setSaveAsNewSecretKey(e.target.value)}
              placeholder="e.g. my-tool-api-key"
              helperText="Optional; leave empty to auto-generate"
            />
          )}
        </>
      )}
    </>
  );

  return (
    <ComposedModal open onClose={onClose} size="lg" isFullWidth preventCloseOnClickOutside>
      <ModalHeader title={initial ? "Edit tool" : "Add tool"} buttonOnClick={onClose} />
      <form onSubmit={handleSubmit}>
        <ModalBody hasScrollingContent className="add-tool-modal-body">
          {!initial && showTemplates && (
            <div style={{ marginBottom: "1.5rem" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "1rem" }}>
                <Template size={20} />
                <h4 className="cds--type-heading-compact-01">Start from a template</h4>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: "1rem" }}>
                {TOOL_TEMPLATES.map((template) => {
                  const IconComponent = template.icon;
                  return (
                    <ClickableTile
                      key={template.id}
                      onClick={() => applyTemplate(template)}
                      style={{ padding: "1rem" }}
                    >
                      <div style={{ display: "flex", alignItems: "flex-start", gap: "0.75rem" }}>
                        <IconComponent size={24} style={{ flexShrink: 0, marginTop: "0.125rem" }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="cds--type-body-compact-01 cds--type-semibold" style={{ marginBottom: "0.25rem" }}>
                            {template.name}
                          </div>
                          <div className="cds--type-helper-text-01" style={{ color: "var(--cds-text-secondary)" }}>
                            {template.description}
                          </div>
                        </div>
                      </div>
                    </ClickableTile>
                  );
                })}
              </div>
              <div style={{ marginTop: "1rem", paddingTop: "1rem", borderTop: "1px solid var(--cds-border-subtle-01)" }}>
                <Button
                  kind="ghost"
                  size="sm"
                  onClick={() => setShowTemplates(false)}
                >
                  Or configure manually
                </Button>
              </div>
            </div>
          )}
          {!initial && !showTemplates && (
            <div style={{ marginBottom: "1rem" }}>
              <Button
                kind="ghost"
                size="sm"
                renderIcon={Template}
                onClick={() => setShowTemplates(true)}
              >
                Browse templates
              </Button>
            </div>
          )}
          <FormGroup legendText="">
            <TextInput
              id="tool-name"
              labelText="Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={type === "mcp" ? "e.g. my_tool" : "e.g. crm_api"}
              invalid={name.trim() !== "" && !!nameError}
              invalidText={nameError}
              helperText={!nameError || name.trim() === "" ? "Lowercase letters, digits, underscores (e.g. my_tool)" : undefined}
            />
          </FormGroup>
          <FormGroup legendText="">
            <Select
              id="tool-type"
              labelText="Type"
              value={type}
              onChange={(e) => setType(e.target.value as "mcp" | "openapi")}
            >
              <SelectItem value="mcp" text="MCP server" />
              <SelectItem value="openapi" text="OpenAPI service" />
            </Select>
          </FormGroup>
          {type === "mcp" && (
            <FormGroup legendText="">
              <Select
                id="tool-mcp-mode"
                labelText="Connection"
                value={mcpMode}
                onChange={(e) => setMcpMode(e.target.value as McpConnectionMode)}
              >
                <SelectItem value="url" text="URL (SSE)" />
                <SelectItem value="url-http" text="URL (HTTP)" />
                <SelectItem value="command" text="Command (stdio)" />
                <SelectItem value="gateway" text="MCP Gateway instance" />
              </Select>
            </FormGroup>
          )}
          {isGateway ? (
            <FormGroup legendText="">
              <TextInput
                id="tool-gateway-url"
                labelText="Gateway instance URL"
                value={url}
                onChange={(e) => {
                  setUrl(e.target.value);
                  setGatewayServers(null);
                }}
                placeholder="https://<gateway>/?instance=<instance id>"
                invalid={url.trim() !== "" && !gatewayInstanceId}
                invalidText="Copy the URL from the gateway instance page in your workspace; it ends with ?instance=<id>"
                helperText="From the gateway instance page in your workspace"
              />
            </FormGroup>
          ) : type === "mcp" && mcpMode === "command" ? (
            <>
              <FormGroup legendText="">
                <TextInput
                  id="tool-command"
                  labelText="Command"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="e.g. npx"
                />
              </FormGroup>
              <FormGroup legendText="">
                <TextArea
                  id="tool-args"
                  labelText="Args (one per line)"
                  value={argsText}
                  onChange={(e) => setArgsText(e.target.value)}
                  placeholder={"-y\n@modelcontextprotocol/server-filesystem\n./cuga_workspace"}
                  rows={4}
                  helperText="One argument per line (e.g. -y, package name, working directory)"
                />
              </FormGroup>
              <FormGroup legendText="">
                <TextArea
                  id="tool-env"
                  labelText="Environment variables (one per line)"
                  value={envText}
                  onChange={(e) => setEnvText(e.target.value)}
                  placeholder={"API_KEY=your_api_key\nBASE_URL=http://localhost:8000"}
                  rows={3}
                  invalid={!!envError}
                  invalidText={envError}
                  helperText={!envError ? "KEY=VALUE per line. Use ENV_VAR_NAME as value to reference process env (e.g. MY_KEY=MY_KEY)" : undefined}
                />
              </FormGroup>
            </>
          ) : (
            <FormGroup legendText="">
              <TextInput
                id="tool-url"
                labelText="URL"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={
                  type === "mcp"
                    ? mcpMode === "url-http"
                      ? "https://example.com/mcp"
                      : "http://localhost:8112/sse"
                    : "http://localhost:8007/openapi.json"
                }
                required={type === "openapi" || mcpMode === "url" || mcpMode === "url-http"}
                helperText={
                  type === "mcp"
                    ? mcpMode === "url-http"
                      ? "MCP server Streamable HTTP endpoint"
                      : "MCP server SSE endpoint (e.g. /sse)"
                    : "OpenAPI spec URL"
                }
              />
            </FormGroup>
          )}
          <FormGroup legendText="">
            <TextArea
              id="tool-description"
              labelText="Description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short description of what this tool provides"
              rows={2}
              required
            />
          </FormGroup>
          {isGateway ? (
            <FormGroup legendText="Instance API key">
              <p className="cds--type-helper-text-01" style={{ marginBottom: "0.5rem" }}>
                An API key created on the instance page. It is stored as a secret; tool calls run as its service ID.
              </p>
              {secretFields}
              <Button
                kind="tertiary"
                size="sm"
                style={{ marginTop: "0.75rem" }}
                disabled={!gatewayInstanceId || !(gatewayPlainKey || gatewayKeyRef) || gatewayConnecting}
                onClick={connectGateway}
              >
                {gatewayConnecting ? "Connecting…" : gatewayServers ? "Refresh tools" : "Connect"}
              </Button>
              {gatewayError && (
                <InlineNotification kind="error" lowContrast hideCloseButton title="Connect failed" subtitle={gatewayError} />
              )}
              {gatewayServers && (
                <div className="add-tool-gateway-tools" style={{ marginTop: "1rem" }}>
                  <p className="cds--type-label-01" style={{ marginBottom: "0.5rem" }}>
                    Select the tools this agent may use ({gatewaySelected.size} selected)
                  </p>
                  {gatewayServers.length === 0 && <p className="cds--type-helper-text-01">This instance has no tools yet.</p>}
                  {gatewayServers.map((sv) => {
                    const names = sv.tools.map((t) => t.name);
                    const all = names.every((n) => gatewaySelected.has(n));
                    return (
                      <div key={sv.name} style={{ marginBottom: "0.75rem" }}>
                        <Checkbox
                          id={`gw-server-${sv.name}`}
                          labelText={<strong>{sv.name} ({names.length})</strong>}
                          checked={all && names.length > 0}
                          indeterminate={!all && names.some((n) => gatewaySelected.has(n))}
                          onChange={(_e, { checked }) => toggleGatewayTools(names, !!checked)}
                        />
                        <div style={{ paddingLeft: "1.5rem" }}>
                          {sv.tools.map((t) => (
                            <Checkbox
                              key={t.name}
                              id={`gw-tool-${t.name}`}
                              labelText={t.description ? `${t.name} — ${t.description.slice(0, 80)}` : t.name}
                              checked={gatewaySelected.has(t.name)}
                              onChange={(_e, { checked }) => toggleGatewayTools([t.name], !!checked)}
                            />
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              {!gatewayServers && gatewaySelected.size > 0 && (
                <p className="cds--type-helper-text-01" style={{ marginTop: "0.5rem" }}>
                  {gatewaySelected.size} tools selected. Connect to change the selection.
                </p>
              )}
            </FormGroup>
          ) : (
          <FormGroup legendText="Authentication">
            <Select
              id="tool-auth-type"
              labelText="Auth type"
              value={authType}
              onChange={(e) => setAuthType(e.target.value as AuthType)}
            >
              {AUTH_TYPE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} text={opt.label} />
              ))}
            </Select>
            {needsKey && (
              <TextInput
                id="tool-auth-key"
                labelText="Header / query key"
                value={authKey}
                onChange={(e) => setAuthKey(e.target.value)}
                placeholder={authType === "header" ? "X-API-Key" : "api_key"}
              />
            )}
            {authType !== "none" && secretFields}
          </FormGroup>
          )}
        </ModalBody>
        {submitError && (
          <InlineNotification kind="error" lowContrast hideCloseButton title="Not saved" subtitle={submitError} />
        )}
        <ModalFooter>
          <Button kind="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button kind="primary" type="submit" disabled={!valid}>
            {initial ? "Save" : "Add tool"}
          </Button>
        </ModalFooter>
      </form>
    </ComposedModal>
  );
}
