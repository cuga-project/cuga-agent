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
} from "@carbon/react";
import { Template, Folder } from "@carbon/icons-react";
import type { ToolEntry, ToolAuth, AuthType } from "./types/tools";
import { AUTH_TYPE_OPTIONS } from "./types/tools";
import "./AddToolModal.css";

interface AddToolModalProps {
  onClose: () => void;
  onSave: (tool: ToolEntry) => void;
  initial?: ToolEntry | null;
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

export function AddToolModal({ onClose, onSave, initial }: AddToolModalProps) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"mcp" | "openapi">("mcp");
  const [mcpMode, setMcpMode] = useState<McpConnectionMode>("url");
  const [url, setUrl] = useState("");
  const [command, setCommand] = useState("");
  const [argsText, setArgsText] = useState("");
  const [description, setDescription] = useState("");
  const [authType, setAuthType] = useState<AuthType>("none");
  const [authKey, setAuthKey] = useState("");
  const [authValue, setAuthValue] = useState("");
  const [showTemplates, setShowTemplates] = useState(!initial);

  useEffect(() => {
    if (initial) {
      setName(initial.name);
      setType(initial.type);
      setUrl(initial.url ?? "");
      const hasCmd = !!(initial.command?.trim());
      const transport = initial.transport ?? (initial.url ? "sse" : "stdio");
      setMcpMode(hasCmd ? "command" : transport === "http" ? "url-http" : "url");
      setCommand(initial.command ?? "");
      setArgsText((initial.args ?? []).join("\n"));
      setDescription(initial.description ?? "");
      const auth = initial.auth ?? emptyAuth;
      setAuthType(auth.type === "none" || !auth.type ? "none" : auth.type);
      setAuthKey(auth.key ?? "");
      setAuthValue(auth.value ?? "");
      setShowTemplates(false);
    }
  }, [initial]);

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
    setShowTemplates(false);
  };

  const authOption = AUTH_TYPE_OPTIONS.find((o) => o.value === authType);
  const needsKey = authOption?.needsKey ?? false;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const isCommandMcp = type === "mcp" && mcpMode === "command";
    const args = argsText.split("\n").map((s) => s.trim()).filter(Boolean);
    const tool: ToolEntry = {
      name: name.trim() || (type === "mcp" ? "mcp" : "openapi"),
      type,
      url: isCommandMcp ? undefined : (url.trim() || undefined),
      description: description.trim() || undefined,
    };
    if (isCommandMcp) {
      tool.command = command.trim();
      tool.args = args.length ? args : undefined;
      tool.transport = "stdio";
    } else if (type === "mcp" && url.trim()) {
      tool.transport = mcpMode === "url-http" ? "http" : "sse";
    }
    if (authType !== "none" && (needsKey ? authKey.trim() : true)) {
      tool.auth = {
        type: authType,
        ...(needsKey && { key: authKey.trim() }),
        ...(authValue.trim() && { value: authValue.trim() }),
      };
    }
    onSave(tool);
    onClose();
  };

  const isCommandMcp = type === "mcp" && mcpMode === "command";
  const valid = type === "openapi"
    ? url.trim().length > 0
    : isCommandMcp
      ? command.trim().length > 0
      : url.trim().length > 0;

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
              placeholder={type === "mcp" ? "e.g. filesystem" : "e.g. crm"}
              helperText="Display name for this tool or server"
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
              labelText="Description (optional)"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short description of what this tool provides"
              rows={2}
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
            {(authType !== "none" || authValue) && (
              <TextInput
                id="tool-auth-value"
                type="password"
                labelText="Secret / token / value"
                value={authValue}
                onChange={(e) => setAuthValue(e.target.value)}
                placeholder="Leave empty to not store"
                autoComplete="off"
              />
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
