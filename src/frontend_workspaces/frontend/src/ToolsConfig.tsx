import React, { useState, useMemo, useEffect } from "react";
import { Add, Edit, TrashCan, Filter, Key, Search } from "@carbon/icons-react";
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
  InlineNotification,
} from "@carbon/react";
import type { ToolEntry } from "./types/tools";
import { AddToolModal } from "./AddToolModal";
import { SecretsManager } from "./SecretsManager";
import { getForgeCatalog, attachForgeTools, type ForgeCatalog, type ForgeCatalogGateway } from "./api";
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
  builtinTools?: string[];
  onError?: (title: string, message: string) => void;
  onOpenSecrets?: () => void;
}

const TOOLS_PREVIEW_COUNT = 3;
const DEFAULT_BUILTIN_TOOLS = ["knowledge"];

function ToolsConfigInner({ tools, onChange, connectedApps = [], connectedTools = [], agentId = "cuga-default", builtinTools = DEFAULT_BUILTIN_TOOLS, onError, onOpenSecrets }: ToolsConfigProps) {
  const builtinSet = useMemo(() => new Set(builtinTools.map(n => n.toLowerCase())), [builtinTools]);
  const [modalOpen, setModalOpen] = useState(false);
  const [secretsOpen, setSecretsOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [toolsModalIndex, setToolsModalIndex] = useState<number | null>(null);
  const [toolsModalAppName, setToolsModalAppName] = useState<string | null>(null);
  const [showAllTools, setShowAllTools] = useState(false);
  const [forgeCatalog, setForgeCatalog] = useState<ForgeCatalog | null>(null);
  const [forgeModalOpen, setForgeModalOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getForgeCatalog(agentId)
      .then((res) => res.json())
      .then((data: ForgeCatalog) => {
        if (!cancelled) setForgeCatalog(data);
      })
      .catch(() => {
        if (!cancelled) setForgeCatalog({ enabled: false, gateways: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

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

  const handleForgeAttach = async (selections: { slug: string; toolIds: string[] }[]) => {
    let updatedTools = tools;
    for (const { slug, toolIds } of selections) {
      const res = await attachForgeTools(slug, toolIds, agentId);
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        onError?.("Failed to attach workspace tools", body.detail || `${slug}: HTTP ${res.status}`);
        continue;
      }
      // Mirror the entry the server actually persisted rather than
      // reconstructing one. A locally-built stub with url:"" tripped the
      // "url is required" validation AND — worse — the parent's autosave then
      // PATCHed that stub back over the real entry, wiping the URL and
      // credential the attach had just written. auth.value here is a secret
      // reference (env://NAME), not the token.
      const body = await res.json().catch(() => ({}));
      const entry: ToolEntry | undefined = body.entry;
      if (!entry?.name) {
        onError?.("Attach incomplete", `${slug}: server did not return the saved tool entry`);
        continue;
      }
      updatedTools = [...updatedTools.filter((t) => t.name !== entry.name), entry];
    }
    onChange(updatedTools);
    setForgeModalOpen(false);
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

  // Runtime built-in apps (injected by Cuga Lite, not user-configured)
  const runtimeApps = connectedApps.filter(app => app.type === "CUGA_LITE");

  const displayTools = showAllTools ? tools : tools.slice(0, TOOLS_PREVIEW_COUNT);

  return (
    <Stack gap={5} orientation="vertical">
      {tools.length === 0 ? (
        <p className="tools-config-empty">No tools configured yet.</p>
      ) : (
        <Stack gap={3} orientation="vertical" className="tools-config-list">
          {displayTools.map((t, i) => {
            const isConnected = connectedTools.some((ct) => ct.app === t.name);
            const isBuiltIn = builtinSet.has(t.name?.toLowerCase());
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
                    {isBuiltIn && <Tag type="purple" size="sm">Built-in</Tag>}
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
                      disabled={isBuiltIn}
                    />
                    <Button
                      kind="ghost"
                      size="sm"
                      hasIconOnly
                      iconDescription="Remove"
                      renderIcon={TrashCan}
                      onClick={() => handleRemove(i)}
                      disabled={isBuiltIn}
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

      {runtimeApps.length > 0 && (
        <Stack gap={3} orientation="vertical" className="tools-config-list">
          {runtimeApps.map((app) => {
            const appTools = connectedTools.filter((t) => t.app === app.name);
            return (
              <Tile key={app.name} className="tools-config-tile">
                <div className="tools-config-tile-main">
                  <div className="tools-config-tile-info">
                    <span className="tools-config-tile-name">{app.name}</span>
                    <Tag type="purple" size="sm">Built-in</Tag>
                    <Tag type="gray" size="sm">{app.tool_count} tool{app.tool_count !== 1 ? "s" : ""}</Tag>
                  </div>
                </div>
                {appTools.length > 0 && (
                  <p className="tools-config-tile-source" title={appTools.map((t) => t.name).join(", ")}>
                    {appTools.map((t) => t.name).join(", ")}
                  </p>
                )}
              </Tile>
            );
          })}
        </Stack>
      )}

      <HStack gap={3}>
        <Button kind="ghost" size="sm" hasIconOnly iconDescription="Manage secrets" renderIcon={Key} onClick={() => (onOpenSecrets ? onOpenSecrets() : setSecretsOpen(true))} />
        <Button kind="secondary" size="sm" renderIcon={Add} onClick={() => setModalOpen(true)}>
          Add tool
        </Button>
        {forgeCatalog?.enabled && (
          <Button kind="secondary" size="sm" renderIcon={Search} onClick={() => setForgeModalOpen(true)}>
            Browse workspace catalog
          </Button>
        )}
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
          agentId={agentId}
        />
      )}
      {editingIndex !== null && editingTool !== null && (
        <AddToolModal
          key={`edit-${editingIndex}`}
          onClose={() => setEditingIndex(null)}
          onSave={handleEdit}
          initial={editingTool}
          agentId={agentId}
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
      {forgeModalOpen && forgeCatalog?.gateways && (
        <ForgeCatalogModal
          gateways={forgeCatalog.gateways}
          onClose={() => setForgeModalOpen(false)}
          onAttach={handleForgeAttach}
        />
      )}
      <SecretsManager open={secretsOpen} onClose={() => setSecretsOpen(false)} agentId={agentId} />
    </Stack>
  );
}

export const ToolsConfig = React.memo(ToolsConfigInner);

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

interface ForgeCatalogModalProps {
  gateways: ForgeCatalogGateway[];
  onClose: () => void;
  onAttach: (selections: { slug: string; toolIds: string[] }[]) => Promise<void>;
}

function ForgeCatalogModal({ gateways, onClose, onAttach }: ForgeCatalogModalProps) {
  const [selected, setSelected] = useState<Map<string, Set<string>>>(new Map());
  const [attaching, setAttaching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (slug: string, toolId: string) => {
    setSelected((prev) => {
      const next = new Map(prev);
      const set = new Set(next.get(slug) ?? []);
      if (set.has(toolId)) set.delete(toolId);
      else set.add(toolId);
      next.set(slug, set);
      return next;
    });
  };

  const toggleGateway = (gw: ForgeCatalogGateway, checked: boolean) => {
    setSelected((prev) => {
      const next = new Map(prev);
      next.set(gw.slug, checked ? new Set(gw.tools.map((t) => t.id)) : new Set());
      return next;
    });
  };

  const totalSelected = Array.from(selected.values()).reduce((sum, set) => sum + set.size, 0);

  const handleAttach = async () => {
    const selections = gateways
      .map((gw) => ({ slug: gw.slug, toolIds: Array.from(selected.get(gw.slug) ?? []) }))
      .filter((s) => s.toolIds.length > 0);
    if (selections.length === 0) return;
    setAttaching(true);
    setError(null);
    try {
      await onAttach(selections);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to attach workspace tools");
    } finally {
      setAttaching(false);
    }
  };

  return (
    <ComposedModal open onClose={onClose} size="lg" isFullWidth>
      <ModalHeader title="Browse workspace catalog" buttonOnClick={onClose} />
      <ModalBody hasScrollingContent className="server-tools-modal-body">
        {error && (
          <InlineNotification kind="error" title="Attach failed" subtitle={error} hideCloseButton lowContrast />
        )}
        {gateways.length === 0 ? (
          <p className="tools-config-empty">No gateways available in the workspace catalog.</p>
        ) : (
          gateways.map((gw) => {
            const gwSelected = selected.get(gw.slug) ?? new Set<string>();
            const allChecked = gw.tools.length > 0 && gwSelected.size === gw.tools.length;
            return (
              <div key={gw.slug} className="tools-config-tools-checkbox-row" style={{ marginBottom: "1rem" }}>
                <Checkbox
                  id={`forge-gateway-${gw.slug}`}
                  labelText={<strong>{gw.name}</strong>}
                  checked={allChecked}
                  indeterminate={gwSelected.size > 0 && !allChecked}
                  onChange={(_e, { checked }) => toggleGateway(gw, !!checked)}
                />
                <ul className="tools-config-tools-list">
                  {gw.tools.map((t) => (
                    <li key={t.id} className="tools-config-tools-list-item">
                      <Checkbox
                        id={`forge-tool-${gw.slug}-${t.id}`}
                        labelText={
                          <>
                            <span className="tools-config-tool-id">{t.name}</span>
                            {t.description && (
                              <span className="tools-config-tool-desc">
                                {t.description.slice(0, 80)}
                                {t.description.length > 80 ? "…" : ""}
                              </span>
                            )}
                          </>
                        }
                        checked={gwSelected.has(t.id)}
                        onChange={() => toggle(gw.slug, t.id)}
                        title={t.description || t.name}
                      />
                    </li>
                  ))}
                </ul>
              </div>
            );
          })
        )}
      </ModalBody>
      <ModalFooter>
        <Button kind="secondary" onClick={onClose} disabled={attaching}>
          Cancel
        </Button>
        <Button kind="primary" onClick={handleAttach} disabled={attaching || totalSelected === 0}>
          {attaching ? "Attaching…" : `Attach ${totalSelected} tool${totalSelected === 1 ? "" : "s"}`}
        </Button>
      </ModalFooter>
    </ComposedModal>
  );
}
