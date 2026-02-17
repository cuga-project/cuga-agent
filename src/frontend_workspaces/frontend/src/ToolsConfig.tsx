import React, { useState, useMemo } from "react";
import { Tools, Add, Edit, TrashCan, Plug, Filter } from "@carbon/icons-react";
import {
  ComposedModal,
  ModalHeader,
  ModalBody,
  ModalFooter,
  Button,
  Checkbox,
  Stack,
  VStack,
  HStack,
  Tag,
  ContainedList,
  ContainedListItem,
  StructuredListWrapper,
  StructuredListHead,
  StructuredListRow,
  StructuredListCell,
  StructuredListBody,
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
    <Stack gap={5} orientation="vertical">
      {tools.length === 0 && !hasConnected ? (
        <p className="cds--type-body-compact-01 cds--color-text-placeholder">No tools added yet.</p>
      ) : tools.length > 0 ? (
        <StructuredListWrapper>
          <StructuredListBody>
            {(showAllTools ? tools : tools.slice(0, TOOLS_PREVIEW_COUNT)).map((t, i) => (
              <StructuredListRow key={i}>
                <StructuredListCell>
                  <VStack gap={2}>
                    <HStack gap={3}>
                      <span className="cds--type-body-compact-01 cds--type-semibold">
                        {t.name || (t.type === "mcp" ? "MCP" : "OpenAPI")}
                      </span>
                      <Tag type={t.type === "mcp" ? "blue" : "green"} size="md">
                        {t.type === "mcp" ? "MCP" : "OpenAPI"}
                      </Tag>
                      {t.include && t.include.length > 0 && (
                        <Tag type="purple" size="sm">
                          {t.include.length} tool{t.include.length !== 1 ? "s" : ""}
                        </Tag>
                      )}
                    </HStack>
                    {t.url && (
                      <span className="cds--type-helper-text-01" style={{ wordBreak: "break-all" }}>
                        {t.url}
                      </span>
                    )}
                    {t.command && (
                      <span className="cds--type-helper-text-01" style={{ wordBreak: "break-all" }}>
                        {t.command}{t.args?.length ? ` ${t.args.join(" ")}` : ""}
                      </span>
                    )}
                    {t.auth?.type && t.auth.type !== "none" && (
                      <span className="cds--type-helper-text-01">Auth: {t.auth.type}</span>
                    )}
                  </VStack>
                </StructuredListCell>
                <StructuredListCell>
                  <HStack gap={2}>
                    {connectedTools.some((ct) => ct.app === t.name) && (
                      <Button
                        kind="ghost"
                        size="sm"
                        hasIconOnly
                        iconDescription="Select which tools to enable"
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
                      iconDescription="Remove server"
                      renderIcon={TrashCan}
                      onClick={() => handleRemove(i)}
                    />
                  </HStack>
                </StructuredListCell>
              </StructuredListRow>
            ))}
          </StructuredListBody>
        </StructuredListWrapper>
      ) : null}
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
      {hasConnected && (
        <ContainedList
          kind="on-page"
          label={
            <VStack gap={1}>
              <HStack gap={3} className="tools-config-connected-label">
                <Plug size={20} aria-hidden />
                <span className="cds--type-semibold">Connected tools (from registry)</span>
              </HStack>
            </VStack>
          }
          className="tools-config-connected-list"
        >
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
              <ContainedListItem
                key={app.name}
                action={
                  <Button
                    kind="ghost"
                    size="sm"
                    renderIcon={Filter}
                    onClick={() => setToolsModalAppName(app.name)}
                  >
                    Select tools
                  </Button>
                }
              >
                <VStack gap={2}>
                  <HStack gap={3} className="tools-config-connected-item-header">
                    <span className="cds--type-body-compact-01 cds--type-semibold">{app.name}</span>
                    <Tag type="blue" size="md">{app.type}</Tag>
                    {inConfig && (
                      <Tag type="purple" size="md" title="This server is in your configuration above">
                        In config
                      </Tag>
                    )}
                    <span className="cds--type-helper-text-01">
                      {count} tool{count !== 1 ? "s" : ""}
                      {includeSet != null && count < appTools.length ? " selected" : ""}
                    </span>
                  </HStack>
                  {displayTools.length > 0 && (
                   <ContainedList kind="on-page" size="sm" isInset className="tools-config-connected-sublist">
                     {displayTools.slice(0, 3).map((t) => (
                       <ContainedListItem key={t.name} disabled>
                         <span className="cds--type-body-compact-01" title={t.description || t.name}>{t.name}</span>
                       </ContainedListItem>
                     ))}
                     {displayTools.length > 3 && (
                       <ContainedListItem disabled>
                         <span className="cds--type-helper-text-01">+{displayTools.length - 3} more</span>
                       </ContainedListItem>
                     )}
                   </ContainedList>
                 )}
                </VStack>
              </ContainedListItem>
            );
          })}
        </ContainedList>
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
