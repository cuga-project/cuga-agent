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
} from "@carbon/react";
import { Template, Folder } from "@carbon/icons-react";
import type { ToolEntry, ToolAuth, AuthType } from "./types/tools";
import { AUTH_TYPE_OPTIONS } from "./types/tools";
import * as api from "./api";
import "./AddToolModal.css";

interface AddToolModalProps {
  onClose: () => void;
  onSave: (tool: ToolEntry) => void;
  initial?: ToolEntry | null;
  agentId?: string;
}

const emptyAuth: ToolAuth = { type: "none" };

type McpConnectionMode = "url" | "url-http" | "command";

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
    mcpMode: (hasCmd ? "command" : transport === "http" ? "url-http" : "url") as McpConnectionMode,
    url: initial?.url ?? "",
    command: initial?.command ?? "",
    argsText: (initial?.args ?? []).join("\n"),
    description: initial?.description ?? "",
    authType: (!auth.type || auth.type === "none" ? "none" : auth.type) as AuthType,
    authKey: auth.key ?? "",
    authValue: val,
    useSavedSecret: typeof val === "string" && (val.startsWith("db://") || val.startsWith("vault://") || val.startsWith("aws://")),
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

  const authOption = AUTH_TYPE_OPTIONS.find((o) => o.value === authType);
  const needsKey = authOption?.needsKey ?? false;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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
          : `${name.trim() || "tool"}-${authType}-${Date.now()}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");
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

  const valid = !nameError && !envError && description.trim().length > 0 && (
    type === "openapi"
      ? url.trim().length > 0
      : isCommandMcp
        ? command.trim().length > 0
        : url.trim().length > 0
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
              </Select>
            </FormGroup>
          )}
          {type === "mcp" && mcpMode === "command" ? (
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
            {authType !== "none" && (
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
                                : `${name.trim() || "tool"}-${authType}-${Date.now()}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");
                              const slug = baseSlug || `${name.trim() || "tool"}-${authType}-${Date.now()}`.toLowerCase().replace(/[^a-z0-9-]/g, "-");
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
                    <Checkbox
                      id="tool-save-as-secret"
                      labelText="Save as new secret"
                      checked={saveAsNewSecret}
                      onChange={(_e, { checked }) => setSaveAsNewSecret(!!checked)}
                    />
                    {saveAsNewSecret && (
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
            )}
          </FormGroup>
        </ModalBody>
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
