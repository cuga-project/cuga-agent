import React, { useState, useEffect } from "react";
import { X } from "lucide-react";
import type { ToolEntry, ToolAuth, AuthType } from "./types/tools";
import { AUTH_TYPE_OPTIONS } from "./types/tools";
import "./AddToolModal.css";

interface AddToolModalProps {
  onClose: () => void;
  onSave: (tool: ToolEntry) => void;
  initial?: ToolEntry | null;
}

const emptyAuth: ToolAuth = { type: "none" };

export function AddToolModal({ onClose, onSave, initial }: AddToolModalProps) {
  const [name, setName] = useState("");
  const [type, setType] = useState<"mcp" | "openapi">("mcp");
  const [url, setUrl] = useState("");
  const [description, setDescription] = useState("");
  const [authType, setAuthType] = useState<AuthType>("none");
  const [authKey, setAuthKey] = useState("");
  const [authValue, setAuthValue] = useState("");

  useEffect(() => {
    if (initial) {
      setName(initial.name);
      setType(initial.type);
      setUrl(initial.url ?? "");
      setDescription(initial.description ?? "");
      const auth = initial.auth ?? emptyAuth;
      setAuthType(auth.type === "none" || !auth.type ? "none" : auth.type);
      setAuthKey(auth.key ?? "");
      setAuthValue(auth.value ?? "");
    }
  }, [initial]);

  const authOption = AUTH_TYPE_OPTIONS.find((o) => o.value === authType);
  const needsKey = authOption?.needsKey ?? false;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const tool: ToolEntry = {
      name: name.trim() || (type === "mcp" ? "mcp" : "openapi"),
      type,
      url: url.trim(),
      description: description.trim() || undefined,
    };
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

  const valid = url.trim().length > 0;

  return (
    <div className="tool-modal-overlay" onClick={onClose}>
      <div className="tool-modal" onClick={(e) => e.stopPropagation()}>
        <div className="tool-modal-header">
          <h2>{initial ? "Edit tool" : "Add tool"}</h2>
          <button type="button" className="tool-modal-close" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="tool-modal-body">
            <div className="tool-modal-field">
              <label>Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={type === "mcp" ? "e.g. filesystem" : "e.g. crm"}
              />
              <small>Display name for this tool or server</small>
            </div>
            <div className="tool-modal-field">
              <label>Type</label>
              <select value={type} onChange={(e) => setType(e.target.value as "mcp" | "openapi")}>
                <option value="mcp">MCP server</option>
                <option value="openapi">OpenAPI service</option>
              </select>
            </div>
            <div className="tool-modal-field">
              <label>URL</label>
              <input
                type="text"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={type === "mcp" ? "http://localhost:8112/sse" : "http://localhost:8007/openapi.json"}
                required
              />
              <small>{type === "mcp" ? "MCP server SSE or HTTP endpoint" : "OpenAPI spec URL"}</small>
            </div>
            <div className="tool-modal-field">
              <label>Description (optional)</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Short description of what this tool provides"
                rows={2}
              />
            </div>
            <div className="tool-modal-auth-section">
              <label className="tool-modal-field" style={{ marginBottom: 8 }}>Authentication</label>
              <div className="tool-modal-field">
                <label>Auth type</label>
                <select
                  value={authType}
                  onChange={(e) => setAuthType(e.target.value as AuthType)}
                >
                  {AUTH_TYPE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
              {needsKey && (
                <div className="tool-modal-field">
                  <label>Header / query key</label>
                  <input
                    type="text"
                    value={authKey}
                    onChange={(e) => setAuthKey(e.target.value)}
                    placeholder={authType === "header" ? "X-API-Key" : "api_key"}
                  />
                </div>
              )}
              {(authType !== "none" || authValue) && (
                <div className="tool-modal-field">
                  <label>Secret / token / value</label>
                  <input
                    type="password"
                    value={authValue}
                    onChange={(e) => setAuthValue(e.target.value)}
                    placeholder="Leave empty to not store"
                    autoComplete="off"
                  />
                </div>
              )}
            </div>
          </div>
          <div className="tool-modal-footer">
            <button type="button" className="tool-modal-btn tool-modal-btn-cancel" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="tool-modal-btn tool-modal-btn-save" disabled={!valid}>
              {initial ? "Save" : "Add tool"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
