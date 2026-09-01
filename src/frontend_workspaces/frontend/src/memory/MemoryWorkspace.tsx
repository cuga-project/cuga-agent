import React, { useMemo, useState } from "react";
import {
  Button,
  Column,
  Grid,
  Search,
  Select,
  SelectItem,
} from "@carbon/react";
import { ArrowRight, Close, Renew } from "@carbon/icons-react";
import {
  deleteMemory,
  loadAdminMemoryPage,
  loadMemoryPage,
  loadProtectionStatus,
  loadRetentionCapabilities,
  loadRetentionRuns,
  runRetention,
} from "./api";
import {
  type MemoryRecord,
  type ProtectionStatus,
  type RetentionCapabilities,
  type RetentionReportItem,
  type RetentionRun,
} from "./types";
import "./memory.scss";

type MemorySort =
  | "recently-saved"
  | "recently-used"
  | "most-used"
  | "least-used"
  | "oldest"
  | "name";

type AdminTab = "automation" | "memory" | "activity";
type AutomationId = ProtectionStatus["id"] | "retention" | "events";

type AutomationItem = {
  id: AutomationId;
  title: string;
  description: string;
  status: string;
  detail: string;
  kind: "protection" | "retention" | "events";
  enabled: boolean;
  healthy?: boolean;
  pluginCount?: number;
};

type MemoryWorkspaceProps = {
  agentId: string;
  agentName: string;
  onClose: () => void;
  canManage?: boolean;
  focusEntityIds?: string[];
  focusRelationship?: "used" | "saved";
  onClearFocus?: () => void;
  onOpenConversation?: (threadId: string) => void;
};

function displayType(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function statusTone(status: string): "healthy" | "warning" | "neutral" | "error" {
  if (status === "Needs attention" || status === "Protected") {
    return "warning";
  }
  if (status === "Incomplete" || status === "Failed") return "error";
  if (status === "Unavailable" || status === "Status unavailable" || status === "Disabled") return "neutral";
  return "healthy";
}

function runStatus(run: RetentionRun): string {
  if (run.status !== "completed" || run.errors.length > 0) return "Incomplete";
  return "Completed";
}

function formatRule(rule: RetentionCapabilities["rules"][number]): string {
  if (rule.description) return rule.description;
  const action = rule.action === "delete" ? "Delete" : rule.action === "flag" ? "Flag" : displayType(rule.action);
  const days = rule.maxUnusedDays ?? rule.maxAgeDays;
  const qualifier = rule.maxUnusedDays != null ? " without use" : "";
  return `${action} ${displayType(rule.entityType).toLowerCase()} memories${days != null ? ` after ${days} days${qualifier}` : ""}`;
}

function memoryStatusDetail(
  memory: MemoryRecord,
  capabilities: RetentionCapabilities | null,
): string {
  if (memory.state !== "Needs attention") return memory.statusDetail;
  const rule = capabilities?.rules.find((candidate) => candidate.name === memory.retentionRule);
  if (!rule) return memory.statusDetail;
  const memoryType = displayType(rule.entityType).toLowerCase();
  if (rule.maxUnusedDays != null) {
    return `Flagged because this ${memoryType} memory has not been used for ${rule.maxUnusedDays} days.`;
  }
  if (rule.maxAgeDays != null) {
    return `Flagged because this ${memoryType} memory is more than ${rule.maxAgeDays} days old.`;
  }
  return `Flagged because it matched the ${displayType(rule.name).toLowerCase()} retention rule.`;
}

function recordDomId(scope: string, id: string): string {
  return `memory-record-${scope}-${id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function DefinitionList({
  items,
}: {
  items: Array<{ label: string; value: React.ReactNode }>;
}) {
  return (
    <dl className="memory-workspace__definition-list">
      {items.map((item) => (
        <React.Fragment key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

function ReferenceLink({
  children,
  href,
  onClick,
}: {
  children: React.ReactNode;
  href: string;
  onClick: () => void;
}) {
  return (
    <a
      href={href}
      onClick={(event) => {
        event.preventDefault();
        onClick();
      }}
    >
      {children}
    </a>
  );
}

function RecordRow({
  id,
  scope,
  selected,
  title,
  meta,
  status,
  detail,
  onSelect,
  muted = false,
}: {
  id: string;
  scope: string;
  selected: boolean;
  title: string;
  meta: string;
  status: string;
  detail: string;
  onSelect: () => void;
  muted?: boolean;
}) {
  return (
    <button
      id={recordDomId(scope, id)}
      type="button"
      className="memory-workspace__record"
      data-selected={selected}
      aria-pressed={selected}
      data-muted={muted}
      onClick={onSelect}
    >
      <span className="memory-workspace__record-copy">
        <strong>{title}</strong>
        <span>{meta}</span>
      </span>
      <span className="memory-workspace__record-state">
        <strong className={`memory-workspace__status memory-workspace__status--${statusTone(status)}`}>
          {status}
        </strong>
        <span title={detail}>{detail}</span>
      </span>
      <ArrowRight size={16} aria-hidden="true" />
    </button>
  );
}

function MasterDetail({
  listLabel,
  list,
  detail,
  detailLabel,
  sheetOpen,
  closeSheet,
}: {
  listLabel: string;
  list: React.ReactNode;
  detail: React.ReactNode;
  detailLabel: string;
  sheetOpen: boolean;
  closeSheet: () => void;
}) {
  return (
    <Grid className="memory-workspace__master-detail">
      <Column sm={4} md={8} lg={9} className="memory-workspace__record-list">
        <section aria-label={listLabel}>{list}</section>
      </Column>
      <Column sm={4} md={8} lg={7} className="memory-workspace__detail-column">
        <aside
          className="memory-workspace__detail"
          data-open={sheetOpen}
          aria-label={detailLabel}
        >
          <button
            type="button"
            className="memory-workspace__detail-close"
            aria-label="Close details"
            title="Close details"
            onClick={closeSheet}
          >
            <Close size={20} />
          </button>
          {detail}
        </aside>
        <button
          type="button"
          className="memory-workspace__scrim"
          aria-label="Close details"
          onClick={closeSheet}
        />
      </Column>
    </Grid>
  );
}

function DetailHeader({
  eyebrow,
  title,
  status,
}: {
  eyebrow: string;
  title: string;
  status?: string;
}) {
  return (
    <div className="memory-workspace__detail-head">
      <p className="memory-workspace__eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      {status && (
        <span className={`memory-workspace__detail-status memory-workspace__status--${statusTone(status)}`}>
          {status}
        </span>
      )}
    </div>
  );
}

function MemoryDetail({
  memory,
  capabilities,
  deleting,
  onDelete,
  onOpenConversation,
  admin = false,
}: {
  memory: MemoryRecord;
  capabilities: RetentionCapabilities | null;
  deleting?: boolean;
  onDelete?: () => void;
  onOpenConversation?: (threadId: string) => void;
  admin?: boolean;
}) {
  const source = memory.sourceConversationId && onOpenConversation ? (
    <ReferenceLink
      href={`/chat?thread_id=${encodeURIComponent(memory.sourceConversationId)}`}
      onClick={() => onOpenConversation(memory.sourceConversationId!)}
    >
      {memory.sourceLabel}
    </ReferenceLink>
  ) : memory.sourceLabel;

  return (
    <>
      <DetailHeader
        eyebrow={admin ? "Lifecycle detail" : "Selected memory"}
        title={memory.title}
        status={memory.state === "Retained" ? "Current" : memory.state}
      />
      <div className="memory-workspace__detail-body">
        {!admin && memory.content && (
          <div className="memory-workspace__notice">
            <strong>Remembered information</strong>
            <p>{memory.content}</p>
          </div>
        )}
        <DefinitionList
          items={[
            ...(admin && memory.ownerLabel ? [{ label: "Owner", value: memory.ownerLabel }] : []),
            { label: "Type", value: displayType(memory.entityType) },
            ...(memory.category ? [{ label: "Category", value: memory.category }] : []),
            { label: "Source", value: source },
            { label: "Saved", value: memory.createdLabel },
            {
              label: "Use frequency",
              value: `Used ${memory.usageCount} ${memory.usageCount === 1 ? "time" : "times"}`,
            },
            { label: "Last used", value: memory.lastUsedLabel },
            { label: "Status", value: memoryStatusDetail(memory, capabilities) },
            {
              label: "Related",
              value: memory.relatedIds.length
                ? `${memory.relatedIds.length} related ${memory.relatedIds.length === 1 ? "memory" : "memories"}`
                : "No linked memories",
            },
          ]}
        />
        {memory.recentUsage.length > 0 && (
          <section className="memory-workspace__recent-usage">
            <h3>Recent use</h3>
            <ul>
              {memory.recentUsage.map((usage, index) => (
                <li key={`${usage.threadId}-${usage.usedAt}-${index}`}>
                  {usage.threadId && onOpenConversation ? (
                    <ReferenceLink
                      href={`/chat?thread_id=${encodeURIComponent(usage.threadId)}`}
                      onClick={() => onOpenConversation(usage.threadId)}
                    >
                      {usage.conversationLabel}
                    </ReferenceLink>
                  ) : usage.conversationLabel}
                  <span>{usage.usedLabel}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {memory.legalHold && (
          <div className="memory-workspace__notice">
            <strong>Deletion unavailable</strong>
            <p>This memory is protected by a legal hold.</p>
          </div>
        )}
        {admin ? (
          <div className="memory-workspace__notice memory-workspace__notice--muted">
            <strong>Content hidden</strong>
            <p>Stored content is not available in the administrator view.</p>
          </div>
        ) : (
          <div className="memory-workspace__detail-actions">
            <Button
              kind="danger"
              size="sm"
              disabled={memory.legalHold || deleting}
              onClick={onDelete}
            >
              {deleting ? "Deleting..." : "Forget"}
            </Button>
          </div>
        )}
      </div>
    </>
  );
}

function AutomationDetail({
  automation,
  capabilities,
  latestRun,
  runningRetention,
  onRunRetention,
}: {
  automation: AutomationItem;
  capabilities: RetentionCapabilities | null;
  latestRun?: RetentionRun;
  runningRetention: boolean;
  onRunRetention: () => void;
}) {
  const latestLabel = latestRun ? new Date(latestRun.createdAt).toLocaleString() : "None recorded";
  return (
    <>
      <DetailHeader eyebrow="Automation" title={automation.title} status={automation.status} />
      <div className="memory-workspace__detail-body">
        <p>{automation.description}</p>
        {automation.kind === "protection" && (
          <DefinitionList
            items={[
              { label: "Status", value: automation.enabled ? "Enabled" : "Disabled" },
              { label: "Health", value: automation.healthy ? "Healthy" : "Status unavailable" },
              {
                label: "Configuration",
                value: automation.pluginCount
                  ? `${automation.pluginCount} protection ${automation.pluginCount === 1 ? "plugin" : "plugins"}`
                  : "No protection plugins reported",
              },
              { label: "Operation", value: "Continuous" },
            ]}
          />
        )}
        {automation.kind === "retention" && (
          <>
            <DefinitionList
              items={[
                { label: "Availability", value: capabilities?.available ? "Manual runs available" : "Unavailable" },
                { label: "Schedule", value: "Manual only" },
                { label: "Latest manual activity", value: latestLabel },
                { label: "Next occurrence", value: "Not scheduled" },
              ]}
            />
            <section className="memory-workspace__rules">
              <h3>Published rules</h3>
              {capabilities?.rules.length ? (
                <ul>{capabilities.rules.map((rule) => <li key={rule.name}>{formatRule(rule)}</li>)}</ul>
              ) : <p>No retention rules are available.</p>}
            </section>
          </>
        )}
        {automation.kind === "events" && (
          <>
            <DefinitionList
              items={[
                { label: "Status", value: "Unavailable" },
                { label: "Destination", value: "Not configured" },
                { label: "Latest", value: "No lifecycle event delivered" },
              ]}
            />
            <div className="memory-workspace__notice memory-workspace__notice--muted">
              <strong>Events integration required</strong>
              <p>Lifecycle delivery will become configurable when the events feature is available.</p>
            </div>
          </>
        )}
        <div className="memory-workspace__detail-actions">
          {automation.kind === "retention" && (
            <>
              <Button kind="danger--tertiary" size="sm" disabled={runningRetention || !capabilities?.available} onClick={onRunRetention}>
                {runningRetention ? "Running..." : "Run retention now"}
              </Button>
              <Button kind="secondary" size="sm" disabled>Edit schedule</Button>
            </>
          )}
          {automation.kind === "events" && <Button kind="secondary" size="sm" disabled>Configure destination</Button>}
        </div>
      </div>
    </>
  );
}

function ReportItems({
  title,
  items,
  memories,
  capabilities,
  onOpenMemory,
}: {
  title: string;
  items: RetentionReportItem[];
  memories: MemoryRecord[];
  capabilities: RetentionCapabilities | null;
  onOpenMemory: (memoryId: string) => void;
}) {
  if (!items.length) return null;
  return (
    <section className="memory-workspace__report-items">
      <h3>{title}</h3>
      <ul>
        {items.map((item, index) => {
          const memory = memories.find((candidate) => candidate.entityId === item.entityId);
          const title = memory?.title ?? item.title;
          const outcome = item.outcome ? displayType(item.outcome) : undefined;
          const itemType = item.entityType ? displayType(item.entityType) : undefined;
          return (
            <li key={`${item.entityId ?? "unknown"}-${index}`}>
              {memory ? (
                <ReferenceLink
                  href={`/chat?memory_id=${encodeURIComponent(memory.entityId)}`}
                  onClick={() => onOpenMemory(memory.id)}
                >
                  <strong>{memory.title}</strong>
                </ReferenceLink>
              ) : (
                <strong>{title ?? "Memory record unavailable"}</strong>
              )}
              <span>
                {memory && memory.state === "Needs attention"
                  ? memoryStatusDetail(memory, capabilities)
                  : [itemType, outcome].filter(Boolean).join(" / ") ||
                    (item.entityId ? `Memory ${item.entityId}` : "Unknown memory")}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function RetentionRunDetail({
  run,
  memories,
  capabilities,
  onOpenMemory,
}: {
  run: RetentionRun;
  memories: MemoryRecord[];
  capabilities: RetentionCapabilities | null;
  onOpenMemory: (memoryId: string) => void;
}) {
  const status = runStatus(run);
  return (
    <>
      <DetailHeader
        eyebrow={new Date(run.createdAt).toLocaleString()}
        title="Retention run"
        status={status}
      />
      <div className="memory-workspace__detail-body">
        <p>{run.summary}</p>
        {run.warnings.map((warning) => (
          <div className="memory-workspace__notice" key={warning}>
            <strong>Warning</strong>
            <p>{warning}</p>
          </div>
        ))}
        {run.errors.map((error) => (
          <div className="memory-workspace__notice memory-workspace__notice--error" key={error}>
            <strong>Error</strong>
            <p>{error}</p>
          </div>
        ))}
        <DefinitionList
          items={[
            { label: "Run ID", value: run.runId },
            { label: "Mode", value: "Applied changes" },
            { label: "Requested by", value: run.actorId || "Service administrator" },
            { label: "Result", value: status },
          ]}
        />
        <ReportItems title="Flagged for review" items={run.flagged} memories={memories} capabilities={capabilities} onOpenMemory={onOpenMemory} />
        <ReportItems title="Deleted" items={run.deleted} memories={memories} capabilities={capabilities} onOpenMemory={onOpenMemory} />
        <ReportItems title="Skipped" items={run.skipped} memories={memories} capabilities={capabilities} onOpenMemory={onOpenMemory} />
      </div>
    </>
  );
}

export function MemoryWorkspace({
  agentId,
  agentName,
  onClose,
  canManage = false,
  focusEntityIds = [],
  focusRelationship = "used",
  onClearFocus,
  onOpenConversation,
}: MemoryWorkspaceProps) {
  const rootRef = React.useRef<HTMLElement>(null);
  const [view, setView] = useState<"user" | "admin">("user");
  const [adminTab, setAdminTab] = useState<AdminTab>("automation");
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [memoryTotal, setMemoryTotal] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<RetentionCapabilities | null>(null);
  const [protections, setProtections] = useState<ProtectionStatus[]>([]);
  const [adminMemories, setAdminMemories] = useState<MemoryRecord[]>([]);
  const [adminMemoryTotal, setAdminMemoryTotal] = useState(0);
  const [adminNextCursor, setAdminNextCursor] = useState<string | null>(null);
  const [runs, setRuns] = useState<RetentionRun[]>([]);
  const [selectedMemoryId, setSelectedMemoryId] = useState("");
  const [selectedAdminMemoryId, setSelectedAdminMemoryId] = useState("");
  const [selectedAutomationId, setSelectedAutomationId] = useState<AutomationId>("save-check");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [detailOpen, setDetailOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [entityType, setEntityType] = useState("all");
  const [adminOwner, setAdminOwner] = useState("all");
  const [adminState, setAdminState] = useState("all");
  const [sort, setSort] = useState<MemorySort>("recently-saved");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [runningRetention, setRunningRetention] = useState(false);
  const [message, setMessage] = useState("");

  const refreshData = React.useCallback(async () => {
    setLoading(true);
    try {
      const [page, retention, history, adminPage, protectionStatus] = await Promise.all([
        loadMemoryPage(agentId),
        loadRetentionCapabilities(),
        canManage ? loadRetentionRuns(agentId) : Promise.resolve([]),
        canManage ? loadAdminMemoryPage(agentId) : Promise.resolve(null),
        canManage ? loadProtectionStatus() : Promise.resolve([]),
      ]);
      setMemories(page.items);
      setMemoryTotal(page.total);
      setNextCursor(page.nextCursor);
      setCapabilities(retention);
      setRuns(history);
      setProtections(protectionStatus);
      if (adminPage) {
        setAdminMemories(adminPage.items);
        setAdminMemoryTotal(adminPage.total);
        setAdminNextCursor(adminPage.nextCursor);
        setSelectedAdminMemoryId((current) =>
          adminPage.items.some((memory) => memory.id === current) ? current : adminPage.items[0]?.id ?? "",
        );
      }
      setSelectedMemoryId((current) =>
        page.items.some((memory) => memory.id === current) ? current : page.items[0]?.id ?? "",
      );
      setSelectedRunId((current) =>
        history.some((run) => run.runId === current) ? current : history[0]?.runId ?? "",
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Memory data is unavailable");
    } finally {
      setLoading(false);
    }
  }, [agentId, canManage]);

  React.useEffect(() => {
    void refreshData();
  }, [refreshData]);

  React.useEffect(() => {
    rootRef.current?.scrollTo({ top: 0, behavior: "instant" });
    setDetailOpen(false);
  }, [view]);

  const entityTypes = useMemo(
    () => Array.from(new Set(memories.map((memory) => memory.entityType))).sort(),
    [memories],
  );

  const adminOwners = useMemo(
    () => Array.from(new Set(adminMemories.map((memory) => memory.ownerLabel).filter((owner): owner is string => Boolean(owner)))).sort(),
    [adminMemories],
  );

  const visibleAdminMemories = useMemo(
    () => adminMemories.filter((memory) =>
      (adminOwner === "all" || memory.ownerLabel === adminOwner) &&
      (adminState === "all" || memory.state === adminState),
    ),
    [adminMemories, adminOwner, adminState],
  );

  React.useEffect(() => {
    if (!visibleAdminMemories.some((memory) => memory.id === selectedAdminMemoryId)) {
      setSelectedAdminMemoryId(visibleAdminMemories[0]?.id ?? "");
    }
  }, [selectedAdminMemoryId, visibleAdminMemories]);

  const automations = useMemo<AutomationItem[]>(() => {
    const protectionItems: AutomationItem[] = (["save-check", "send-check"] as const).map((id) => {
      const protection = protections.find((item) => item.id === id);
      const title = id === "save-check" ? "Sensitive information before saving" : "Sensitive information before sending";
      const description = id === "save-check"
        ? "Checks every memory before it is stored and stops saves rejected by configured protection plugins."
        : "Checks messages and tool inputs before they are sent to the AI model.";
      return {
        id,
        title: protection?.title ?? title,
        description: protection?.description ?? description,
        status: protection?.enabled && protection.healthy ? "Enabled and healthy" : protection?.enabled ? "Needs attention" : "Status unavailable",
        detail: "Continuous",
        kind: "protection",
        enabled: protection?.enabled ?? false,
        healthy: protection?.healthy ?? false,
        pluginCount: protection?.pluginCount ?? 0,
      };
    });
    return [
      ...protectionItems,
      {
        id: "retention",
        title: "Retention",
        description: "Evaluates the published retention rules on demand.",
        status: capabilities?.available ? "Available for manual runs" : "Unavailable",
        detail: "Manual only",
        kind: "retention",
        enabled: capabilities?.available ?? false,
      },
      {
        id: "events",
        title: "Lifecycle event delivery",
        description: "Publishes sanitized lifecycle outcomes to an audit, governance, or workflow system.",
        status: "Unavailable",
        detail: "No destination configured",
        kind: "events",
        enabled: false,
      },
    ];
  }, [capabilities?.available, protections]);

  const visibleMemories = useMemo(() => {
    const query = search.trim().toLowerCase();
    const filtered = memories.filter((memory) =>
      (!focusEntityIds.length || focusEntityIds.includes(memory.entityId)) &&
      (entityType === "all" || memory.entityType === entityType) &&
      (!query || `${memory.title} ${memory.entityType} ${memory.category ?? ""} ${memory.sourceLabel}`
        .toLowerCase()
        .includes(query)),
    );
    const time = (value?: string) => value ? Date.parse(value) || 0 : 0;
    return [...filtered].sort((left, right) => {
      if (sort === "recently-used") return time(right.lastUsedAt) - time(left.lastUsedAt);
      if (sort === "most-used") return right.usageCount - left.usageCount;
      if (sort === "least-used") return left.usageCount - right.usageCount;
      if (sort === "oldest") return time(left.createdAt) - time(right.createdAt);
      if (sort === "name") return left.title.localeCompare(right.title);
      return time(right.createdAt) - time(left.createdAt);
    });
  }, [entityType, focusEntityIds, memories, search, sort]);

  React.useEffect(() => {
    if (!visibleMemories.some((memory) => memory.id === selectedMemoryId)) {
      setSelectedMemoryId(visibleMemories[0]?.id ?? "");
    }
  }, [selectedMemoryId, visibleMemories]);

  const selectedMemory = visibleMemories.find((memory) => memory.id === selectedMemoryId) ?? visibleMemories[0];
  const selectedAdminMemory = visibleAdminMemories.find((memory) => memory.id === selectedAdminMemoryId) ?? visibleAdminMemories[0];
  const selectedAutomation = automations.find((automation) => automation.id === selectedAutomationId) ?? automations[0];
  const selectedRun = runs.find((run) => run.runId === selectedRunId) ?? runs[0];

  React.useEffect(() => {
    if (!detailOpen) return;
    const selectedId = view === "user"
      ? selectedMemory?.id
      : adminTab === "automation"
        ? selectedAutomation?.id
        : adminTab === "memory"
          ? selectedAdminMemory?.id
          : selectedRun?.runId;
    if (!selectedId) return;
    window.requestAnimationFrame(() => {
      rootRef.current
        ?.querySelector<HTMLElement>(`#${recordDomId(view === "user" ? "user" : adminTab, selectedId)}`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }, [adminTab, detailOpen, selectedAdminMemory?.id, selectedAutomation?.id, selectedMemory?.id, selectedRun?.runId, view]);

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await loadMemoryPage(agentId, nextCursor);
      setMemories((current) => {
        const byId = new Map(current.map((memory) => [memory.id, memory]));
        page.items.forEach((memory) => byId.set(memory.id, memory));
        return Array.from(byId.values());
      });
      setMemoryTotal(page.total);
      setNextCursor(page.nextCursor);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "More memories could not be loaded");
    } finally {
      setLoadingMore(false);
    }
  };

  const loadMoreAdminMemories = async () => {
    if (!adminNextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await loadAdminMemoryPage(agentId, adminNextCursor);
      setAdminMemories((current) => {
        const byId = new Map(current.map((memory) => [memory.id, memory]));
        page.items.forEach((memory) => byId.set(memory.id, memory));
        return Array.from(byId.values());
      });
      setAdminMemoryTotal(page.total);
      setAdminNextCursor(page.nextCursor);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "More memories could not be loaded");
    } finally {
      setLoadingMore(false);
    }
  };

  const forgetSelectedMemory = async () => {
    if (!selectedMemory || deleting) return;
    if (selectedMemory.legalHold) {
      setMessage("This memory is protected by a legal hold");
      return;
    }
    if (!window.confirm("Forget this memory? Its source conversation will remain available.")) return;
    setDeleting(true);
    try {
      await deleteMemory(selectedMemory.entityId, agentId);
      setMessage("Memory deleted");
      setDetailOpen(false);
      await refreshData();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Memory could not be deleted");
    } finally {
      setDeleting(false);
    }
  };

  const executeRetention = async () => {
    if (runningRetention || !capabilities?.available) return;
    if (!window.confirm(
      "Run retention now? Memories matching deletion rules may be permanently deleted.",
    )) return;
    setRunningRetention(true);
    setMessage("Running retention...");
    try {
      const report = await runRetention(agentId);
      await refreshData();
      setSelectedRunId(report.runId ?? "");
      setMessage("Retention run completed");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Retention could not be completed");
    } finally {
      setRunningRetention(false);
    }
  };

  const memoryList = (
    <div>
      <div className="memory-workspace__list-head">
        <h2>What the agent remembers</h2>
        <p>Select a memory to inspect its source and controls.</p>
        {focusEntityIds.length > 0 && (
          <Button kind="ghost" size="sm" onClick={onClearFocus}>
            Showing {focusEntityIds.length} {focusRelationship} in the response. Show all
          </Button>
        )}
      </div>
      {visibleMemories.length > 0 ? (
        <ul className="memory-workspace__list">
          {visibleMemories.map((memory) => (
            <li key={memory.id}>
              <RecordRow
                id={memory.id}
                scope="user"
                selected={memory.id === selectedMemory?.id}
                title={memory.title}
                meta={`${memory.sourceLabel} / Used ${memory.usageCount} ${memory.usageCount === 1 ? "time" : "times"}`}
                status={memory.state === "Retained" ? "Current" : memory.state}
                detail={memory.state === "Needs attention"
                  ? memoryStatusDetail(memory, capabilities)
                  : memory.category ?? displayType(memory.entityType)}
                onSelect={() => {
                  setSelectedMemoryId(memory.id);
                  setDetailOpen(true);
                }}
              />
            </li>
          ))}
        </ul>
      ) : (
        <p className="memory-workspace__empty">
          {loading ? "Loading memories..." : "No memories match these filters."}
        </p>
      )}
      {nextCursor && !focusEntityIds.length && (
        <div className="memory-workspace__load-more">
          <Button kind="ghost" size="sm" disabled={loadingMore} onClick={() => void loadMore()}>
            {loadingMore ? "Loading..." : "Load more"}
          </Button>
        </div>
      )}
    </div>
  );

  return (
    <main ref={rootRef} className="memory-workspace">
      {message && (
        <div className="memory-workspace__message" role="status" aria-live="polite">
          <span>{message}</span>
          <button type="button" aria-label="Dismiss message" title="Dismiss message" onClick={() => setMessage("")}>
            <Close size={16} />
          </button>
        </div>
      )}

      {view === "user" ? (
        <>
          <div className="memory-workspace__context-bar">
            <div>
              <strong>{agentName}</strong>
              <span>Your view of this agent&apos;s memory</span>
            </div>
            <div className="memory-workspace__context-actions">
              <Button kind="ghost" size="sm" onClick={onClose}>Back to chat</Button>
              {canManage && (
                <Button kind="secondary" size="sm" renderIcon={ArrowRight} onClick={() => setView("admin")}>
                  Administration
                </Button>
              )}
            </div>
          </div>

          <Grid className="memory-workspace__page-head">
            <Column sm={4} md={5} lg={11} className="memory-workspace__page-copy">
              <p className="memory-workspace__eyebrow">Your memory</p>
              <h1>Memory</h1>
              <p>Review what {agentName} remembers about you and delete memories that are no longer useful.</p>
            </Column>
            <Column sm={4} md={3} lg={5} className="memory-workspace__summary">
              <strong>{memoryTotal}</strong>
              <span>memories about you</span>
              {capabilities && (
                <>
                  <p>{capabilities.rules.length} published retention {capabilities.rules.length === 1 ? "rule" : "rules"}</p>
                  <p>{capabilities.scheduleLabel}</p>
                </>
              )}
            </Column>
          </Grid>

          <Grid className="memory-workspace__toolbar">
            <Column sm={4} md={8} lg={5}>
              <Search
                id="memory-search"
                size="lg"
                labelText="Search your memories"
                placeholder={`Search ${memories.length} loaded memories`}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </Column>
            <Column sm={4} md={4} lg={3}>
              <Select id="memory-type" labelText="Type" value={entityType} onChange={(event) => setEntityType(event.target.value)}>
                <SelectItem value="all" text="All types" />
                {entityTypes.map((type) => <SelectItem key={type} value={type} text={displayType(type)} />)}
              </Select>
            </Column>
            <Column sm={4} md={4} lg={4}>
              <Select
                id="memory-sort"
                labelText="Sort"
                value={sort}
                onChange={(event) => {
                  setSort(event.target.value as MemorySort);
                  setDetailOpen(false);
                }}
              >
                <SelectItem value="recently-saved" text="Recently saved" />
                <SelectItem value="recently-used" text="Recently used" />
                <SelectItem value="most-used" text="Most used" />
                <SelectItem value="least-used" text="Least used" />
                <SelectItem value="oldest" text="Oldest" />
                <SelectItem value="name" text="Name" />
              </Select>
            </Column>
            <Column sm={4} md={8} lg={4} className="memory-workspace__toolbar-summary">
              <span>Showing {visibleMemories.length} of {memoryTotal}</span>
              <Button kind="ghost" size="sm" renderIcon={Renew} disabled={loading} onClick={() => void refreshData()}>
                Refresh
              </Button>
            </Column>
          </Grid>

          <MasterDetail
            listLabel="Your memories"
            list={memoryList}
            detail={selectedMemory ? (
              <MemoryDetail
                memory={selectedMemory}
                capabilities={capabilities}
                deleting={deleting}
                onDelete={() => void forgetSelectedMemory()}
                onOpenConversation={onOpenConversation}
              />
            ) : <p className="memory-workspace__empty">Select a memory to view its details.</p>}
            detailLabel="Memory details"
            sheetOpen={detailOpen}
            closeSheet={() => setDetailOpen(false)}
          />
        </>
      ) : (
        <>
          <div className="memory-workspace__context-bar">
            <div>
              <strong>{agentName} / Memory administration</strong>
              <span>Administrator controls</span>
            </div>
            <div className="memory-workspace__context-actions">
              <Button kind="ghost" size="sm" onClick={onClose}>Back to chat</Button>
              <Button kind="secondary" size="sm" onClick={() => setView("user")}>Your memory</Button>
            </div>
          </div>

          <div className="memory-workspace__tabs" role="tablist" aria-label="Memory administration">
            {(["automation", "memory", "activity"] as AdminTab[]).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={adminTab === tab}
                onClick={() => {
                  setAdminTab(tab);
                  setDetailOpen(false);
                }}
              >
                {tab[0].toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </div>

          {adminTab === "automation" && (
            <div role="tabpanel" aria-label="Automation">
              <Grid className="memory-workspace__page-head memory-workspace__page-head--admin">
                <Column sm={4} md={8} lg={16} className="memory-workspace__page-copy">
                  <p className="memory-workspace__eyebrow">Published configuration</p>
                  <h1>Automation</h1>
                  <p>Review protection status and run retention manually. Scheduling and lifecycle delivery are visible but unavailable in this release.</p>
                </Column>
              </Grid>

              <section className="memory-workspace__section">
                <div className="memory-workspace__section-head">
                  <div>
                    <h2>Automation status</h2>
                    <p>Select an automation to inspect its current configuration.</p>
                  </div>
                  <Button kind="ghost" size="sm" renderIcon={Renew} disabled={loading} onClick={() => void refreshData()}>Refresh</Button>
                </div>
                <MasterDetail
                  listLabel="Automation status"
                  list={(
                    <ul className="memory-workspace__list">
                      {automations.map((automation) => (
                        <li key={automation.id}>
                          <RecordRow
                            id={automation.id}
                            scope="automation"
                            selected={automation.id === selectedAutomation?.id}
                            title={automation.title}
                            meta={automation.description}
                            status={automation.status}
                            detail={automation.detail}
                            muted={automation.kind === "events"}
                            onSelect={() => {
                              setSelectedAutomationId(automation.id);
                              setDetailOpen(true);
                            }}
                          />
                        </li>
                      ))}
                    </ul>
                  )}
                  detail={selectedAutomation ? (
                    <AutomationDetail
                      automation={selectedAutomation}
                      capabilities={capabilities}
                      latestRun={runs[0]}
                      runningRetention={runningRetention}
                      onRunRetention={() => void executeRetention()}
                    />
                  ) : <p className="memory-workspace__empty">Select an automation to view its details.</p>}
                  detailLabel="Automation details"
                  sheetOpen={detailOpen}
                  closeSheet={() => setDetailOpen(false)}
                />
              </section>

              <section className="memory-workspace__section">
                <div className="memory-workspace__section-head">
                  <div>
                    <h2>Latest activity</h2>
                    <p>Recent manual retention runs for this agent.</p>
                  </div>
                </div>
                <div className="memory-workspace__compact-list">
                  {runs.slice(0, 3).length ? (
                    <ul className="memory-workspace__list">
                      {runs.slice(0, 3).map((run) => (
                        <li key={run.runId}>
                          <RecordRow
                            id={run.runId}
                            scope="latest"
                            selected={false}
                            title="Retention run"
                            meta={`${new Date(run.createdAt).toLocaleString()} / ${run.runId}`}
                            status={runStatus(run)}
                            detail={run.summary}
                            onSelect={() => {
                              setSelectedRunId(run.runId);
                              setAdminTab("activity");
                              setDetailOpen(true);
                            }}
                          />
                        </li>
                      ))}
                    </ul>
                  ) : <p className="memory-workspace__empty">{loading ? "Loading activity..." : "No retention activity has been recorded."}</p>}
                </div>
              </section>

              <section className="memory-workspace__section memory-workspace__section--disabled" aria-disabled="true">
                <div className="memory-workspace__section-head">
                  <div>
                    <h2>Lifecycle event records</h2>
                    <p>Sanitized outcomes can be delivered to an audit, governance, or workflow system after the events feature is integrated.</p>
                  </div>
                  <Button kind="secondary" size="sm" disabled>Configure destination</Button>
                </div>
                <div className="memory-workspace__disabled-surface">
                  <strong>No event destination connected</strong>
                  <p>Lifecycle event records will appear here when delivery is available.</p>
                </div>
              </section>
            </div>
          )}

          {adminTab === "memory" && (
            <div role="tabpanel" aria-label="Memory">
              <Grid className="memory-workspace__page-head memory-workspace__page-head--admin">
                <Column sm={4} md={8} lg={16} className="memory-workspace__page-copy">
                  <p className="memory-workspace__eyebrow">Governed inventory</p>
                  <h1>Agent memory</h1>
                  <p>Inspect lifecycle metadata across the agent&apos;s memory inventory. Stored content is not shown in this view.</p>
                </Column>
              </Grid>
              <Grid className="memory-workspace__toolbar">
                <Column sm={4} md={4} lg={5}>
                  <Select id="admin-memory-owner" labelText="Memory about" value={adminOwner} onChange={(event) => setAdminOwner(event.target.value)}>
                    <SelectItem value="all" text="All owners" />
                    {adminOwners.map((owner) => <SelectItem key={owner} value={owner} text={owner} />)}
                  </Select>
                </Column>
                <Column sm={4} md={4} lg={5}>
                  <Select id="admin-memory-state" labelText="Lifecycle state" value={adminState} onChange={(event) => setAdminState(event.target.value)}>
                    <SelectItem value="all" text="All states" />
                    <SelectItem value="Needs attention" text="Needs attention" />
                    <SelectItem value="Retained" text="Retained" />
                    <SelectItem value="Protected" text="Protected" />
                  </Select>
                </Column>
                <Column sm={4} md={8} lg={6} className="memory-workspace__toolbar-summary">
                  <span>Showing {visibleAdminMemories.length} of {adminMemoryTotal}</span>
                  <Button kind="ghost" size="sm" renderIcon={Renew} disabled={loading} onClick={() => void refreshData()}>Refresh</Button>
                </Column>
              </Grid>
              <MasterDetail
                listLabel="Admin memory list"
                list={(
                  <div>
                    {visibleAdminMemories.length ? (
                      <ul className="memory-workspace__list">
                        {visibleAdminMemories.map((memory) => (
                          <li key={memory.id}>
                            <RecordRow
                              id={memory.id}
                              scope="memory"
                              selected={memory.id === selectedAdminMemory?.id}
                              title={memory.title}
                              meta={`${memory.ownerLabel ?? "Owner unavailable"} / ${memory.sourceLabel}`}
                              status={memory.state}
                              detail={memory.state === "Needs attention"
                                ? memoryStatusDetail(memory, capabilities)
                                : memory.category ?? displayType(memory.entityType)}
                              onSelect={() => {
                                setSelectedAdminMemoryId(memory.id);
                                setDetailOpen(true);
                              }}
                            />
                          </li>
                        ))}
                      </ul>
                    ) : <p className="memory-workspace__empty">{loading ? "Loading memories..." : "No memories match these filters."}</p>}
                    {adminNextCursor && adminOwner === "all" && adminState === "all" && (
                      <div className="memory-workspace__load-more">
                        <Button kind="ghost" size="sm" disabled={loadingMore} onClick={() => void loadMoreAdminMemories()}>
                          {loadingMore ? "Loading..." : "Load more"}
                        </Button>
                      </div>
                    )}
                  </div>
                )}
                detail={selectedAdminMemory
                  ? <MemoryDetail memory={selectedAdminMemory} capabilities={capabilities} admin onOpenConversation={onOpenConversation} />
                  : <p className="memory-workspace__empty">Select a memory to view its details.</p>}
                detailLabel="Admin memory details"
                sheetOpen={detailOpen}
                closeSheet={() => setDetailOpen(false)}
              />
            </div>
          )}

          {adminTab === "activity" && (
            <div role="tabpanel" aria-label="Activity">
              <Grid className="memory-workspace__page-head memory-workspace__page-head--admin">
                <Column sm={4} md={8} lg={16} className="memory-workspace__page-copy">
                  <p className="memory-workspace__eyebrow">Lifecycle evidence</p>
                  <h1>Activity</h1>
                  <p>Inspect the locally recorded history of manual retention runs.</p>
                </Column>
              </Grid>
              <div className="memory-workspace__section-head">
                <div>
                  <h2>Retention history</h2>
                  <p>{runs.length} activity {runs.length === 1 ? "record" : "records"}</p>
                </div>
                <Button kind="ghost" size="sm" renderIcon={Renew} disabled={loading} onClick={() => void refreshData()}>Refresh</Button>
              </div>
              <MasterDetail
                listLabel="Retention activity history"
                list={runs.length ? (
                  <ul className="memory-workspace__list">
                    {runs.map((run) => (
                      <li key={run.runId}>
                        <RecordRow
                          id={run.runId}
                          scope="activity"
                          selected={run.runId === selectedRun?.runId}
                          title="Retention run"
                          meta={`${new Date(run.createdAt).toLocaleString()} / ${run.runId}`}
                          status={runStatus(run)}
                          detail={run.summary}
                          onSelect={() => {
                            setSelectedRunId(run.runId);
                            setDetailOpen(true);
                          }}
                        />
                      </li>
                    ))}
                  </ul>
                ) : <p className="memory-workspace__empty">{loading ? "Loading activity..." : "No retention activity has been recorded."}</p>}
                detail={selectedRun ? (
                  <RetentionRunDetail
                    run={selectedRun}
                    memories={adminMemories}
                    capabilities={capabilities}
                    onOpenMemory={(memoryId) => {
                      setSelectedAdminMemoryId(memoryId);
                      setAdminTab("memory");
                      setDetailOpen(true);
                    }}
                  />
                ) : <p className="memory-workspace__empty">Select an activity record to view its details.</p>}
                detailLabel="Activity details"
                sheetOpen={detailOpen}
                closeSheet={() => setDetailOpen(false)}
              />
            </div>
          )}
        </>
      )}
    </main>
  );
}
