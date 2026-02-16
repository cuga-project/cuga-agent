import React, { useState, useMemo } from "react";
import { Wrench, Plus, Pencil, Trash2, Plug, ListFilter } from "lucide-react";
import { ComposedModal, ModalHeader, ModalBody, ModalFooter, Button, Checkbox } from "@carbon/react";
import type { ToolEntry } from "./types/tools";
import { AddToolModal } from "./AddToolModal";
import "./ToolsConfig.css";

export interface ConnectedTool {
  name: string;
  id: string;
  app: string;
  app_type: string;
  description: string;
}

export interface ConnectedApp {
  name: string;
  type: string;
  tool_count: number;
}

interface ToolsConfigProps {
  tools: ToolEntry[];
  onChange: (tools: ToolEntry[]) => void;
  connectedApps?: ConnectedApp[];
  connectedTools?: ConnectedTool[];
}

const TOOLS_PREVIEW_COUNT = 3;

export function ToolsConfig({ tools, onChange, connectedApps = [], connectedTools = [] }: ToolsConfigProps) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [toolsModalIndex, setToolsModalIndex] = useState<number | null>(null);
  const [toolsModalAppName, setToolsModalAppName] = useState<string | null>(null);
  const [showAllTools, setShowAllTools] = useState(false);

  const handleAdd = (tool: ToolEntry) => {
    onChange([...tools, tool]);
    setModalOpen(false);
  };

  const handleEdit = (tool: ToolEntry) => {
    if (editingIndex === null) return;
    const next = [...tools];
    next[editingIndex] = tool;
    onChange(next);
    setEditingIndex(null);
  };

  const handleRemove = (index: number) => {
    onChange(tools.filter((_, i) => i !== index));
  };

  const updateServerInclude = (index: number, include: string[] | undefined) => {
    const next = tools.map((t, i) => {
      if (i !== index) return t;
      if (include && include.length > 0) return { ...t, include };
      const { include: _omit, ...rest } = t;
      return rest;
    });
    onChange(next);
  };

  const saveToolsModalByAppName = (appName: string, include: string[] | undefined) => {
    const idx = tools.findIndex((t) => t.name === appName);
    if (idx >= 0) {
      const next = tools.map((t, i) => {
        if (i !== idx) return t;
        if (include && include.length > 0) return { ...t, include };
        const { include: _omit, ...rest } = t;
        return rest;
      });
      onChange(next);
    } else {
      const entry: ToolEntry = {
        name: appName,
        type: "mcp",
        url: "",
        description: "",
      };
      if (include && include.length > 0) entry.include = include;
      const connectedIndex = connectedApps.findIndex((a) => a.name === appName);
      if (connectedIndex >= 0) {
        const byConnectedOrder = [...tools];
        let insertAt = 0;
        for (const app of connectedApps) {
          if (app.name === appName) break;
          if (tools.some((t) => t.name === app.name)) insertAt++;
        }
        byConnectedOrder.splice(insertAt, 0, entry);
        onChange(byConnectedOrder);
      } else {
        onChange([...tools, entry]);
      }
    }
  };

  const editingTool = editingIndex !== null ? tools[editingIndex] ?? null : null;
  const hasConnected = connectedApps.length > 0 || connectedTools.length > 0;
  const toolsModalServer =
    toolsModalIndex !== null ? tools[toolsModalIndex] ?? null : null;
  const toolsModalOpenByApp = toolsModalAppName !== null;
  const toolsModalServerName = toolsModalOpenByApp
    ? toolsModalAppName
    : toolsModalServer?.name ?? null;
  const toolsModalAppTools = useMemo(
    () =>
      toolsModalServerName
        ? connectedTools.filter((t) => t.app === toolsModalServerName)
        : [],
    [toolsModalServerName, connectedTools]
  );
  const toolsModalCurrentInclude = useMemo(
    () =>
      toolsModalServerName
        ? tools.find((t) => t.name === toolsModalServerName)?.include
        : undefined,
    [toolsModalServerName, tools]
  );
  const closeToolsModal = () => {
    setToolsModalIndex(null);
    setToolsModalAppName(null);
  };

  return (
    <section className="manage-section tools-config-section">
      <h3>
        <Wrench size={14} style={{ verticalAlign: "middle", marginRight: 6 }} />
        Tools
      </h3>
      <div className="tools-config-list">
        {tools.length === 0 && !hasConnected ? (
          <div className="tools-config-empty">No tools added yet.</div>
        ) : tools.length > 0 ? (
          (showAllTools ? tools : tools.slice(0, TOOLS_PREVIEW_COUNT)).map((t, i) => (
            <div key={i} className="tools-config-card">
              <div className="tools-config-card-main">
                <span className="tools-config-name">{t.name || (t.type === "mcp" ? "MCP" : "OpenAPI")}</span>
                <span className={`tools-config-badge tools-config-badge-${t.type}`}>
                  {t.type === "mcp" ? "MCP" : "OpenAPI"}
                </span>
              </div>
              {t.url && (
                <div className="tools-config-url" title={t.url}>
                  {t.url}
                </div>
              )}
              {t.command && (
                <div className="tools-config-command" title={[t.command, ...(t.args ?? [])].join(" ")}>
                  {t.command}{t.args?.length ? ` ${t.args.join(" ")}` : ""}
                </div>
              )}
              {t.auth?.type && t.auth.type !== "none" && (
                <div className="tools-config-auth-hint">Auth: {t.auth.type}</div>
              )}
              {t.include && t.include.length > 0 && (
                <div className="tools-config-include-hint">
                  {t.include.length} tool{t.include.length !== 1 ? "s" : ""} enabled
                </div>
              )}
              <div className="tools-config-actions">
                {connectedTools.some((ct) => ct.app === t.name) && (
                  <button
                    type="button"
                    className="tools-config-action-btn"
                    onClick={() => setToolsModalIndex(i)}
                    title="Select which tools to enable"
                  >
                    <ListFilter size={14} />
                  </button>
                )}
                <button
                  type="button"
                  className="tools-config-action-btn"
                  onClick={() => setEditingIndex(i)}
                  title="Edit"
                >
                  <Pencil size={14} />
                </button>
                <button
                  type="button"
                  className="tools-config-action-btn tools-config-action-btn-danger"
                  onClick={() => handleRemove(i)}
                  title="Remove server"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))
        ) : null}
      </div>
      <div className="tools-config-list-footer">
        <button type="button" className="tools-config-add-btn" onClick={() => setModalOpen(true)}>
          <Plus size={14} />
          Add
        </button>
        {tools.length > TOOLS_PREVIEW_COUNT && !showAllTools && (
          <button
            type="button"
            className="tools-config-more-btn"
            onClick={() => setShowAllTools(true)}
          >
            + {tools.length - TOOLS_PREVIEW_COUNT} more…
          </button>
        )}
        {tools.length > TOOLS_PREVIEW_COUNT && showAllTools && (
          <button
            type="button"
            className="tools-config-more-btn"
            onClick={() => setShowAllTools(false)}
          >
            Show less
          </button>
        )}
      </div>
      {hasConnected && (
        <div className="tools-config-connected">
          <h4 className="tools-config-connected-title">
            <Plug size={12} style={{ verticalAlign: "middle", marginRight: 4 }} />
            Connected tools (from registry)
          </h4>
          <p className="tools-config-connected-hint">
            Servers the registry discovered. Use &quot;Select tools&quot; to choose which tools to enable; that adds the server to your configuration above if it isn&apos;t there yet.
          </p>
          <div className="tools-config-connected-list">
            {connectedApps.map((app) => {
              const appTools = connectedTools.filter((t) => t.app === app.name);
              const serverEntry = tools.find((t) => t.name === app.name);
              const inConfig = !!serverEntry;
              const includeSet =
                serverEntry?.include && serverEntry.include.length > 0
                  ? new Set(serverEntry.include)
                  : null;
              const displayTools =
                includeSet != null
                  ? appTools.filter(
                      (t) => includeSet.has(t.id ?? t.name) || includeSet.has(t.name)
                    )
                  : appTools;
              const count = displayTools.length;
              return (
                <div key={app.name} className="tools-config-connected-app">
                  <div className="tools-config-connected-app-header">
                    <span className="tools-config-connected-app-name">{app.name}</span>
                    <span className="tools-config-badge tools-config-badge-mcp">{app.type}</span>
                    {inConfig && (
                      <span className="tools-config-in-config-badge" title="This server is in your configuration above">
                        In config
                      </span>
                    )}
                    <span className="tools-config-connected-app-count">
                      {count} tool{count !== 1 ? "s" : ""}
                      {includeSet != null && count < appTools.length ? " selected" : ""}
                    </span>
                    <button
                      type="button"
                      className="tools-config-connected-select-btn"
                      onClick={() => setToolsModalAppName(app.name)}
                      title="Select which tools to enable"
                    >
                      <ListFilter size={14} />
                      Select tools
                    </button>
                  </div>
                  {displayTools.length > 0 && (
                    <ul className="tools-config-connected-tool-names">
                      {displayTools.slice(0, 3).map((t) => (
                        <li key={t.name} title={t.description || t.name}>
                          {t.name}
                        </li>
                      ))}
                      {displayTools.length > 3 && (
                        <li>+{displayTools.length - 3} more</li>
                      )}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {modalOpen && (
        <AddToolModal
          onClose={() => setModalOpen(false)}
          onSave={handleAdd}
          initial={null}
        />
      )}
      {editingIndex !== null && (
        <AddToolModal
          onClose={() => setEditingIndex(null)}
          onSave={handleEdit}
          initial={editingTool}
        />
      )}
      {toolsModalServerName && (
        <ServerToolsModal
          serverName={toolsModalServerName}
          appTools={toolsModalAppTools}
          currentInclude={toolsModalCurrentInclude}
          isNewInConfig={toolsModalOpenByApp && !tools.some((t) => t.name === toolsModalServerName)}
          onClose={closeToolsModal}
          onSave={(include) => {
            if (toolsModalOpenByApp && toolsModalAppName) {
              saveToolsModalByAppName(toolsModalAppName, include);
            } else if (toolsModalIndex !== null) {
              updateServerInclude(toolsModalIndex, include);
            }
            closeToolsModal();
          }}
        />
      )}
    </section>
  );
}

interface ServerToolsModalProps {
  serverName: string;
  appTools: ConnectedTool[];
  currentInclude: string[] | undefined;
  isNewInConfig?: boolean;
  onClose: () => void;
  onSave: (include: string[] | undefined) => void;
}

function ServerToolsModal({
  serverName,
  appTools,
  currentInclude,
  isNewInConfig,
  onClose,
  onSave,
}: ServerToolsModalProps) {
  const allIds = useMemo(() => appTools.map((t) => t.id ?? t.name), [appTools]);
  const defaultChecked = !currentInclude || currentInclude.length === 0 || currentInclude.length === allIds.length;
  const [selected, setSelected] = useState<Set<string>>(() => {
    if (defaultChecked) return new Set(allIds);
    return new Set(currentInclude ?? []);
  });
  const [selectAll, setSelectAll] = useState(defaultChecked);

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setSelectAll(false);
  };

  const handleSelectAll = (checked: boolean) => {
    setSelectAll(checked);
    setSelected(checked ? new Set(allIds) : new Set());
  };

  const handleSave = () => {
    if (selectAll || selected.size === allIds.length) {
      onSave(undefined);
    } else {
      onSave(Array.from(selected));
    }
  };

  return (
    <ComposedModal open onClose={onClose} size="lg" isFullWidth>
      <ModalHeader title={`Tools for ${serverName}`} buttonOnClick={onClose} />
      <ModalBody hasScrollingContent>
        {isNewInConfig && (
          <p className="tools-config-modal-new-hint">
            Saving will add <strong>{serverName}</strong> to your configuration list above.
          </p>
        )}
        <div className="tools-config-tools-checkbox-row">
          <Checkbox
            id="tools-select-all"
            labelText="Select all"
            checked={selectAll || selected.size === allIds.length}
            onChange={(_e, { checked }) => handleSelectAll(!!checked)}
          />
        </div>
        <ul className="tools-config-tools-list">
          {appTools.map((t) => {
            const id = t.id ?? t.name;
            const checked = selectAll || selected.has(id);
            return (
              <li key={id} className="tools-config-tools-list-item">
                <Checkbox
                  id={`tool-${id}`}
                  labelText={
                    <>
                      <span className="tools-config-tool-id">{id}</span>
                      {t.description && (
                        <span className="tools-config-tool-desc">
                          {t.description.slice(0, 80)}{t.description.length > 80 ? "…" : ""}
                        </span>
                      )}
                    </>
                  }
                  checked={checked}
                  onChange={() => toggle(id)}
                  title={t.description || t.name}
                />
              </li>
            );
          })}
        </ul>
      </ModalBody>
      <ModalFooter>
        <Button kind="secondary" onClick={onClose}>
          Cancel
        </Button>
        <Button kind="primary" onClick={handleSave}>
          Save
        </Button>
      </ModalFooter>
    </ComposedModal>
  );
}
