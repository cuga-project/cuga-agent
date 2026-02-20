import React, { useState, useMemo } from "react";
import { Add, Edit, TrashCan, Filter } from "@carbon/icons-react";
import {
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
  Checkbox,
  Stack,
  HStack,
  Tag,
  Tile,
} from "@carbon/react";
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
  agentId?: string;
  onError?: (title: string, message: string) => void;
}

const TOOLS_PREVIEW_COUNT = 3;

export function ToolsConfig({ tools, onChange, connectedApps = [], connectedTools = [], agentId = "cuga-default", onError }: ToolsConfigProps) {
  const [modalOpen, setModalOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [toolsModalIndex, setToolsModalIndex] = useState<number | null>(null);
  const [toolsModalAppName, setToolsModalAppName] = useState<string | null>(null);
  const [showAllTools, setShowAllTools] = useState(false);

  const handleAdd = (tool: ToolEntry) => {
    const updatedTools = [...tools, tool];
    onChange(updatedTools);
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
    const updatedTools = tools.filter((_, i) => i !== index);
    onChange(updatedTools);
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
    let updatedTools: ToolEntry[];
    
    if (idx >= 0) {
      updatedTools = tools.map((t, i) => {
        if (i !== idx) return t;
        if (include && include.length > 0) return { ...t, include };
        const { include: _omit, ...rest } = t;
        return rest;
      });
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
        updatedTools = [...tools];
        let insertAt = 0;
        for (const app of connectedApps) {
          if (app.name === appName) break;
          if (tools.some((t) => t.name === app.name)) insertAt++;
        }
        updatedTools.splice(insertAt, 0, entry);
      } else {
        updatedTools = [...tools, entry];
      }
    }
    
    onChange(updatedTools);
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

  // Get list of available tools that are NOT yet configured
  const configuredNames = new Set(tools.map(t => t.name));
  const availableToAdd = connectedApps.filter(app => !configuredNames.has(app.name));

  const displayTools = showAllTools ? tools : tools.slice(0, TOOLS_PREVIEW_COUNT);

  return (
    <Stack gap={5} orientation="vertical">
      {tools.length === 0 ? (
        <p className="tools-config-empty">No tools configured yet.</p>
      ) : (
        <Stack gap={3} orientation="vertical" className="tools-config-list">
          {displayTools.map((t, i) => {
            const isConnected = connectedTools.some((ct) => ct.app === t.name);
            const source = t.url || (t.command ? `${t.command}${t.args?.length ? ` ${t.args.join(" ")}` : ""}` : null);
            const hasSubset = t.include && t.include.length > 0;
            return (
              <Tile key={i} className="tools-config-tile">
                <div className="tools-config-tile-main">
                  <div className="tools-config-tile-info">
                    <span className="tools-config-tile-name">{t.name || (t.type === "mcp" ? "MCP" : "OpenAPI")}</span>
                    <Tag type={t.type === "mcp" ? "blue" : "green"} size="sm">
                      {t.type === "mcp" ? "MCP" : "OpenAPI"}
                    </Tag>
                    {isConnected && <span className="tools-config-tile-badge">Connected</span>}
                    {hasSubset && (
                      <span className="tools-config-tile-badge tools-config-tile-badge-subset">
                        {t.include!.length} selected
                      </span>
                    )}
                  </div>
                  <HStack gap={1}>
                    {isConnected && (
                      <Button
                        kind="ghost"
                        size="sm"
                        hasIconOnly
                        iconDescription="Select tools"
                        renderIcon={Filter}
                        onClick={() => setToolsModalIndex(i)}
                      />
                    )}
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription="Edit"
                      renderIcon={Edit}
                      onClick={() => setEditingIndex(i)}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription="Remove"
                      renderIcon={TrashCan}
                      onClick={() => handleRemove(i)}
                    />
                  </HStack>
                </div>
                {source && (
                  <p className="tools-config-tile-source" title={source}>
                    {source.length > 60 ? `${source.slice(0, 60)}…` : source}
                  </p>
                )}
              </Tile>
            );
          })}
        </Stack>
      )}

      <HStack gap={3}>
        <Button kind="secondary" size="sm" renderIcon={Add} onClick={() => setModalOpen(true)}>
          Add tool
        </Button>
        {tools.length > TOOLS_PREVIEW_COUNT && !showAllTools && (
          <Button kind="ghost" size="sm" onClick={() => setShowAllTools(true)}>
            Show {tools.length - TOOLS_PREVIEW_COUNT} more
          </Button>
        )}
        {tools.length > TOOLS_PREVIEW_COUNT && showAllTools && (
          <Button kind="ghost" size="sm" onClick={() => setShowAllTools(false)}>
            Show less
          </Button>
        )}
      </HStack>


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
    </Stack>
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
    setSelected((prev: Set<string>) => {
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
      <ModalBody hasScrollingContent className="server-tools-modal-body">
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
