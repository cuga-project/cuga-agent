import React, { useMemo, useState } from "react";
import {
  Accordion,
  AccordionItem,
  Button,
  Column,
  Grid,
  Search,
  Select,
  SelectItem,
  TabsVertical,
  TabListVertical,
  Tab,
  TabPanels,
  TabPanel,
  UnorderedList,
  ListItem,
} from "@carbon/react";
import { ArrowRight, Close, Renew } from "@carbon/icons-react";
import {
  deleteMemory,
  loadAdminMemoryPage,
  loadMemoryEntity,
  loadMemoryPage,
  loadProtectionStatus,
  loadRetentionCapabilities,
  loadRetentionPolicies,
  loadRetentionRuns,
  runRetention,
  loadRetentionCollection,
  type RetentionCandidate,
  type RetentionAuditEvent,
} from "./api";
import {
  type MemoryRecord,
  type ProtectionStatus,
  type RetentionCapabilities,
  type RetentionPolicy,
  type RetentionReportItem,
  type RetentionRun,
} from "./types";
import "./memory.scss";
import { RetentionSchedules } from "./RetentionSchedules";

type MemorySort =
  | "recently-saved"
  | "recently-used"
  | "most-used"
  | "least-used"
  | "oldest"
  | "name";

type AdminTab = "settings" | "memory" | "activity";
type SettingsId = string;

type SettingsItem = {
  id: SettingsId;
  title: string;
  description: string;
  status: string;
  detail: string;
  kind: "protection" | "retention" | "events";
  enabled: boolean;
  healthy?: boolean;
  pluginCount?: number;
  plugins?: ProtectionStatus["plugins"];
  policy?: RetentionPolicy;
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
  if (status === "Running" || status === "Cancelled") return "neutral";
  if (status === "Interrupted") return "warning";
  if (status === "Unavailable" || status === "Status unavailable" || status === "Disabled") return "neutral";
  return "healthy";
}

function runStatus(run: RetentionRun): string {
  if (run.status === "running") return "Running";
  if (run.status === "interrupted") return "Interrupted";
  if (run.status === "cancelled") return "Cancelled";
  if (run.status === "failed") return "Failed";
  if (run.status !== "completed" || run.errors.length > 0) return "Incomplete";
  return "Completed";
}

function formatRule(rule: RetentionCapabilities["rules"][number]): string {
  if (rule.sourceDeleted && rule.maxAgeDays != null)
    return `Delete memories that are at least ${rule.maxAgeDays} days old if their original conversation has been deleted.`;
  if (rule.description) return rule.description;
  const action =
    rule.action === "delete"
      ? "Delete"
      : rule.action === "flag"
        ? "Flag"
        : displayType(rule.action);
  if (rule.sourceDeleted)
    return `After the source conversation is explicitly deleted, delete memories older than ${rule.maxAgeDays} days.`;
  const days = rule.maxUnusedDays ?? rule.maxAgeDays;
  const qualifier = rule.maxUnusedDays != null ? " without use" : "";
  const entityType = rule.entityType
    ? `${displayType(rule.entityType).toLowerCase()} memories`
    : "all memories";
  return `${action} ${entityType}${days != null ? ` after ${days} days${qualifier}` : ""}`;
}

function memoryStatusDetail(
  memory: MemoryRecord,
  capabilities: RetentionCapabilities | null,
): string {
  if (memory.state !== "Needs attention") return memory.statusDetail;
  const rule = capabilities?.rules.find((candidate) => candidate.name === memory.retentionRule);
  if (!rule) return memory.statusDetail;
  const memoryType = rule.entityType ? displayType(rule.entityType).toLowerCase() : "matching";
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

function SettingsDetail({
  settings,
  capabilities,
  latestRun,
  runningRetention,
  onRunRetention,
}: {
  settings: SettingsItem;
  capabilities: RetentionCapabilities | null;
  latestRun?: RetentionRun;
  runningRetention: boolean;
  onRunRetention: () => void;
}) {
  const policy = settings.policy;
  if (settings.kind === "protection")
    return (
      <section className="memory-settings__filter-group">
        <div className="memory-settings__row">
          <div>
            <h2>
              {settings.id === "save-check"
                ? "Before saving"
                : "Before sending"}
            </h2>
            <p>{settings.description}</p>
          </div>
          <span>{settings.status}</span>
        </div>
        {settings.plugins?.length ? (
          settings.plugins.map((plugin) => (
            <div className="memory-settings__filter" key={plugin.name}>
              <strong>{plugin.name}</strong>
              <span>
                {plugin.enabled ? "Enabled" : "Disabled"} ·{" "}
                {plugin.healthy ? "Healthy" : "Status unavailable"}
              </span>
            </div>
          ))
        ) : (
          <p>No protection plugins reported.</p>
        )}
      </section>
    );
  if (settings.kind === "events")
    return (
      <>
        <p className="memory-settings__intro">
          Connect memory activity to your audit and workflow systems.
        </p>
        <section className="memory-settings__empty">
          <p>Not available yet</p>
          <h2>Event delivery is coming later</h2>
          <p>
            External destinations are not supported yet. Retention outcomes
            remain available in Activity.
          </p>
          <Button disabled kind="tertiary" size="md">
            Configure destination
          </Button>
        </section>
        <p className="memory-settings__note">
          No destination configured · No deliveries recorded
        </p>
      </>
    );
  return (
    <>
      <div className="memory-settings__intro memory-settings__row">
        <p>Manage memory retention for all users of this service instance.</p>
        <Button
          kind="tertiary"
          size="md"
          disabled={
            runningRetention || !capabilities?.available || !policy?.enabled
          }
          onClick={onRunRetention}
        >
          {runningRetention ? "Running retention…" : "Run retention now"}
        </Button>
      </div>
      {policy && (
        <>
          <section className="memory-schedules__rules">
            <h2>Policy rules</h2>
            <UnorderedList>
              {policy.rules.map((rule) => (
                <ListItem key={rule.name}>{formatRule(rule)}</ListItem>
              ))}
            </UnorderedList>
          </section>
          {capabilities?.available ? (
            <RetentionSchedules
              key={policy.policyId}
              policyId={policy.policyId}
              enabled={policy.enabled}
            />
          ) : (
            <p>Retention is unavailable.</p>
          )}
          <section className="memory-schedules__footer">
            <h2>Latest run</h2>
            <p>
              {latestRun
                ? new Date(latestRun.createdAt).toLocaleString()
                : "None recorded"}
            </p>
            <p>View marked memories and completed outcomes in Activity.</p>
          </section>
        </>
      )}
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
      <p>View memories that are still available.</p>
      {!items.some((item) => memories.some((memory) => memory.entityId === item.entityId)) && (
        <p>These memories are not available to view. See audit details for recorded outcomes.</p>
      )}
      <ul>
        {items.map((item, index) => {
          const memory = memories.find((candidate) => candidate.entityId === item.entityId);
          if (!memory) return null;
          const outcome = item.outcome ? displayType(item.outcome) : undefined;
          const itemType = item.entityType ? displayType(item.entityType) : undefined;
          const reason = item.reason?.trim();
          return (
            <li key={`${item.entityId ?? "unknown"}-${index}`}>
              <ReferenceLink
                href={`/chat?memory_id=${encodeURIComponent(memory.entityId)}`}
                onClick={() => onOpenMemory(memory.id)}
              >
                <strong>{memory.title}</strong>
              </ReferenceLink>
              <span>
                {reason || (memory && memory.state === "Needs attention"
                  ? memoryStatusDetail(memory, capabilities)
                  : [itemType, outcome].filter(Boolean).join(" / ") ||
                    (item.entityId ? `Memory ${item.entityId}` : "Unknown memory"))}
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
        title="Retention audit"
        status={status}
      />
      <div className="memory-workspace__detail-body">
        <section className="memory-workspace__notice" aria-label="Recorded retention outcome">
          <strong>{run.deleted.length ? "Memory deletion recorded" : "Retention activity recorded"}</strong>
          <p>{run.summary}</p>
          {run.deleted.length > 0 && <p>Deleted memory titles and contents are not displayed in this history.</p>}
          {run.status !== "completed" && <p>The audit shows the outcomes recorded so far.</p>}
        </section>
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
            { label: "Policy", value: run.policyName ?? run.policyId ?? "Unavailable" },
            { label: "Requested by", value: run.initiatedBy || "Not recorded" },
            { label: "Started", value: new Date(run.startedAt ?? run.createdAt).toLocaleString() },
            { label: "Finished", value: run.completedAt ? new Date(run.completedAt).toLocaleString() : "Not recorded" },
            { label: "Result", value: status },
          ]}
        />
        <ReportItems title="Flagged for review" items={run.flagged} memories={memories} capabilities={capabilities} onOpenMemory={onOpenMemory} />
        <ReportItems title="Skipped" items={run.skipped} memories={memories} capabilities={capabilities} onOpenMemory={onOpenMemory} />
        <Accordion className="memory-workspace__audit-details">
          <AccordionItem title="Administrative audit details">
            <p>Technical references connect recorded actions. They do not retrieve deleted memory content.</p>
            <DefinitionList items={[{ label: "Run ID", value: run.runId }, { label: "Policy ID", value: run.policyId ?? "Not recorded" }]} />
            <div className="memory-workspace__audit-table">
              <table>
                <caption>Recorded actions for this run</caption>
                <thead><tr><th scope="col">Outcome</th><th scope="col">Entity reference</th><th scope="col">Reason</th></tr></thead>
                <tbody>
                  {([
                    ["Flagged", run.flagged], ["Deleted", run.deleted], ["Skipped", run.skipped],
                  ] as const).flatMap(([outcome, items]) => items.map((item, index) => (
                    <tr key={`${outcome}-${item.entityId ?? index}`}>
                      <td>{outcome}</td>
                      <td><code>{item.entityId ?? "Not recorded"}</code></td>
                      <td>{item.reason || "Not recorded"}</td>
                    </tr>
                  )))}
                </tbody>
              </table>
            </div>
          </AccordionItem>
        </Accordion>
      </div>
    </>
  );
}

function CollectionActivity({memories, refreshKey, onOpen}: {
  memories: MemoryRecord[]; refreshKey: RetentionRun[]; onOpen: (memoryId: string) => void;
}) {
  const [candidates, setCandidates] = useState<RetentionCandidate[]>([]);
  const [events, setEvents] = useState<RetentionAuditEvent[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  React.useEffect(() => {
    let active = true;
    setLoading(true);
    loadRetentionCollection().then((data) => {
      if (active) {setCandidates(data.candidates); setEvents(data.audit); setError("");}
    }).catch(() => {if (active) setError("Retention activity could not be loaded.");})
      .finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, [refreshKey]);
  const pending = candidates.filter((item) => ["pending", "held", "review"].includes(item.status));
  const visible = pending.flatMap((item) => {
    const memory = memories.find((memory) => memory.entityId === item.entity_id);
    return memory ? [{item, memory}] : [];
  });
  const groups = Array.from(events.reduce((groups, event) => {
    const key = `${event.occurred_at.slice(0, 10)}:${event.policy_id}:${event.outcome}`;
    const existing = groups.get(key);
    if (existing) existing.count++;
    else groups.set(key, {event, count: 1});
    return groups;
  }, new Map<string, {event: RetentionAuditEvent; count: number}>()).values());
  return <section className="memory-workspace__section" aria-label="Durable retention activity">
    <h2>Marked memories</h2>
    {error && <p role="alert">{error}</p>}
    {loading && <p>Loading retention activity…</p>}
    {!loading && !error && !pending.length && <p>No memories are currently marked.</p>}
    <ul className="memory-workspace__list">
      {visible.map(({item, memory}) => <li key={`${item.policy_id}:${item.entity_id}`}>
        <Button kind="ghost" onClick={() => onOpen(memory.id)}>{memory.title}</Button>
        <p>{item.status === "held" ? "Deletion blocked by legal hold" : item.status === "review" ? "Flagged for review" : "Awaiting deletion"} · {item.policy_id}</p>
      </li>)}
    </ul>
    {pending.length > visible.length && <p>Other marked memories are outside the current memory view. Their status is available in audit details.</p>}
    <h2>Committed outcomes</h2>
    <p>Outcomes remain available even when a run is interrupted.</p>
    <ul className="memory-workspace__list">
      {groups.map(({event, count}) => <li key={event.event_id}>
        <strong>{displayType(event.outcome)} · {event.policy_id}</strong>
        <p>{count} {count === 1 ? "memory" : "memories"} · {new Date(event.occurred_at).toLocaleDateString()}</p>
      </li>)}
    </ul>
    <Accordion><AccordionItem title="Candidate and action references">
      <p>Showing up to 1,000 recent candidates and actions for this service instance.</p>
      <div className="memory-workspace__audit-table"><table>
        <caption>Current marks</caption><thead><tr><th scope="col">Reference</th><th scope="col">Status</th><th scope="col">Policy</th></tr></thead>
        <tbody>{pending.map((item) => <tr key={`${item.policy_id}:${item.entity_id}`}><td><code>{item.entity_id}</code></td><td>{item.status === "held" ? "Deletion blocked by legal hold" : displayType(item.status)}</td><td>{item.policy_id}</td></tr>)}</tbody>
      </table><table>
        <caption>Committed actions</caption><thead><tr><th scope="col">Reference</th><th scope="col">Outcome</th><th scope="col">Requested by</th><th scope="col">Time</th></tr></thead>
        <tbody>{events.map((event) => <tr key={event.event_id}><td><code>{event.entity_id}</code></td><td>{displayType(event.outcome)}</td><td>{event.initiated_by ?? "Not recorded"}</td><td>{new Date(event.occurred_at).toLocaleString()}</td></tr>)}</tbody>
      </table></div>
    </AccordionItem></Accordion>
  </section>;
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
  const requestGenerationRef = React.useRef(0);
  const activeAgentRef = React.useRef(agentId);
  const [view, setView] = useState<"user" | "admin">("user");
  const [adminTab, setAdminTab] = useState<AdminTab>("settings");
  const [memories, setMemories] = useState<MemoryRecord[]>([]);
  const [memoryTotal, setMemoryTotal] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<RetentionCapabilities | null>(null);
  const [retentionPolicies, setRetentionPolicies] = useState<RetentionPolicy[]>([]);
  const [protections, setProtections] = useState<ProtectionStatus[]>([]);
  const [adminMemories, setAdminMemories] = useState<MemoryRecord[]>([]);
  const [adminMemoryTotal, setAdminMemoryTotal] = useState(0);
  const [adminNextCursor, setAdminNextCursor] = useState<string | null>(null);
  const [runs, setRuns] = useState<RetentionRun[]>([]);
  const [selectedMemoryId, setSelectedMemoryId] = useState("");
  const [selectedAdminMemoryId, setSelectedAdminMemoryId] = useState("");
  const [selectedSettingsId, setSelectedSettingsId] = useState<SettingsId>("");
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
  const focusEntityKey = Array.from(new Set(focusEntityIds.filter(Boolean))).join("\0");

  React.useLayoutEffect(() => {
    if (activeAgentRef.current === agentId) return;
    activeAgentRef.current = agentId;
    requestGenerationRef.current += 1;
    setMemories([]);
    setMemoryTotal(0);
    setNextCursor(null);
    setCapabilities(null);
    setRetentionPolicies([]);
    setProtections([]);
    setAdminMemories([]);
    setAdminMemoryTotal(0);
    setAdminNextCursor(null);
    setRuns([]);
    setSelectedMemoryId("");
    setSelectedAdminMemoryId("");
    setSelectedRunId("");
    setDetailOpen(false);
    setLoadingMore(false);
    setDeleting(false);
    setRunningRetention(false);
  }, [agentId]);

  const refreshData = React.useCallback(async () => {
    const generation = ++requestGenerationRef.current;
    const scopeChanged = activeAgentRef.current !== agentId;
    activeAgentRef.current = agentId;
    setLoading(true);
    setLoadingMore(false);
    if (scopeChanged) {
      setMemories([]);
      setMemoryTotal(0);
      setNextCursor(null);
      setCapabilities(null);
      setRetentionPolicies([]);
      setProtections([]);
      setAdminMemories([]);
      setAdminMemoryTotal(0);
      setAdminNextCursor(null);
      setRuns([]);
      setSelectedMemoryId("");
      setSelectedAdminMemoryId("");
      setSelectedRunId("");
      setDetailOpen(false);
      setLoadingMore(false);
      setDeleting(false);
      setRunningRetention(false);
    }
    const focusedEntityIds = focusEntityKey ? focusEntityKey.split("\0") : [];
    const results = await Promise.allSettled([
        loadMemoryPage(agentId),
        loadRetentionCapabilities(),
        canManage ? loadRetentionPolicies() : Promise.resolve([]),
        canManage ? loadRetentionRuns() : Promise.resolve([]),
        canManage ? loadAdminMemoryPage(agentId) : Promise.resolve(null),
        canManage ? loadProtectionStatus() : Promise.resolve([]),
        Promise.allSettled(focusedEntityIds.map((entityId) => loadMemoryEntity(agentId, entityId))),
      ] as const);
    if (generation !== requestGenerationRef.current) return;

    const [page, retention, policies, history, adminPage, protectionStatus, focused] = results;
    const errors: string[] = [];
    const focusedMemories = focused.status === "fulfilled"
      ? focused.value
        .filter((result): result is PromiseFulfilledResult<MemoryRecord> => result.status === "fulfilled")
        .map((result) => result.value)
      : [];
    const unavailableFocusedCount = focused.status === "fulfilled"
      ? focused.value.filter((result) => result.status === "rejected").length
      : focusedEntityIds.length;

    if (page.status === "fulfilled" || focusedMemories.length > 0) {
      const byId = new Map<string, MemoryRecord>();
      if (page.status === "fulfilled") page.value.items.forEach((memory) => byId.set(memory.id, memory));
      focusedMemories.forEach((memory) => byId.set(memory.id, memory));
      const items = Array.from(byId.values());
      setMemories(items);
      setMemoryTotal(page.status === "fulfilled" ? Math.max(page.value.total, items.length) : items.length);
      setNextCursor(page.status === "fulfilled" ? page.value.nextCursor : null);
      setSelectedMemoryId((current) =>
        items.some((memory) => memory.id === current) ? current : items[0]?.id ?? "",
      );
    } else {
      errors.push("Memory inventory is unavailable");
    }
    if (unavailableFocusedCount > 0) {
      errors.push(`${unavailableFocusedCount} referenced ${unavailableFocusedCount === 1 ? "memory is" : "memories are"} no longer available`);
    }

    if (retention.status === "fulfilled") {
      const policyRules = policies.status === "fulfilled" ? policies.value.flatMap((policy) => policy.rules) : [];
      const rulesByName = new Map(retention.value.rules.map((rule) => [rule.name, rule]));
      policyRules.forEach((rule) => rulesByName.set(rule.name, rule));
      setCapabilities({ ...retention.value, rules: Array.from(rulesByName.values()) });
    } else {
      errors.push("Retention status is unavailable");
    }
    if (policies.status === "fulfilled") {
      setRetentionPolicies(policies.value);
    } else {
      errors.push("Retention policies are unavailable");
    }
    if (history.status === "fulfilled") {
      setRuns(history.value);
      setSelectedRunId((current) =>
        history.value.some((run) => run.runId === current) ? current : history.value[0]?.runId ?? "",
      );
    } else {
      errors.push("Retention history is unavailable");
    }
    if (protectionStatus.status === "fulfilled") {
      setProtections(protectionStatus.value);
    } else {
      errors.push("Protection status is unavailable");
    }
    if (adminPage.status === "fulfilled" && adminPage.value) {
      const loadedAdminPage = adminPage.value;
      setAdminMemories(loadedAdminPage.items);
      setAdminMemoryTotal(loadedAdminPage.total);
      setAdminNextCursor(loadedAdminPage.nextCursor);
      setSelectedAdminMemoryId((current) =>
        loadedAdminPage.items.some((memory) => memory.id === current) ? current : loadedAdminPage.items[0]?.id ?? "",
      );
    } else if (adminPage.status === "rejected") {
      errors.push("Administrator memory inventory is unavailable");
    }
    setMessage(errors.join(". "));
    setLoading(false);
  }, [agentId, canManage, focusEntityKey]);

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

  const settingsItems = useMemo<SettingsItem[]>(() => {
    const protectionItems: SettingsItem[] = (["save-check", "send-check"] as const).map((id) => {
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
        plugins: protection?.plugins ?? [],
      };
    });
    const retentionItems: SettingsItem[] = retentionPolicies.map((policy) => ({
      id: `retention:${policy.policyId}`,
      title: policy.name,
      description: policy.description ?? "Evaluates this published retention policy on demand.",
      status: !capabilities?.available ? "Unavailable" : policy.enabled ? "Enabled" : "Disabled",
      detail: `${policy.rules.length} published ${policy.rules.length === 1 ? "rule" : "rules"}`,
      kind: "retention",
      enabled: Boolean(capabilities?.available && policy.enabled),
      policy,
    }));
    return [
      ...protectionItems,
      ...retentionItems,
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
  }, [capabilities?.available, protections, retentionPolicies]);

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
  const settingsCategories = [
    ...settingsItems.filter((item) => item.kind === "retention"),
    { id: "filters", title: "Filters" },
    { id: "events", title: "Lifecycle events" },
  ];
  const settingsIndex = Math.max(
    0,
    settingsCategories.findIndex((item) => item.id === selectedSettingsId),
  );
  const selectedSettings = settingsItems.find(
    (settings) => settings.id === settingsCategories[settingsIndex]?.id,
  );
  const selectedRun = runs.find((run) => run.runId === selectedRunId) ?? runs[0];

  React.useEffect(() => {
    if (!detailOpen) return;
    const selectedId = view === "user"
      ? selectedMemory?.id
      : adminTab === "settings"
        ? selectedSettings?.id
        : adminTab === "memory"
          ? selectedAdminMemory?.id
          : selectedRun?.runId;
    if (!selectedId) return;
    window.requestAnimationFrame(() => {
      rootRef.current
        ?.querySelector<HTMLElement>(`#${recordDomId(view === "user" ? "user" : adminTab, selectedId)}`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }, [adminTab, detailOpen, selectedAdminMemory?.id, selectedSettings?.id, selectedMemory?.id, selectedRun?.runId, view]);

  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
    const generation = requestGenerationRef.current;
    setLoadingMore(true);
    try {
      const page = await loadMemoryPage(agentId, nextCursor);
      if (generation !== requestGenerationRef.current) return;
      setMemories((current) => {
        const byId = new Map(current.map((memory) => [memory.id, memory]));
        page.items.forEach((memory) => byId.set(memory.id, memory));
        return Array.from(byId.values());
      });
      setMemoryTotal(page.total);
      setNextCursor(page.nextCursor);
    } catch (error) {
      if (generation !== requestGenerationRef.current) return;
      setMessage(error instanceof Error ? error.message : "More memories could not be loaded");
    } finally {
      if (generation === requestGenerationRef.current) setLoadingMore(false);
    }
  };

  const loadMoreAdminMemories = async () => {
    if (!adminNextCursor || loadingMore) return;
    const generation = requestGenerationRef.current;
    setLoadingMore(true);
    try {
      const page = await loadAdminMemoryPage(agentId, adminNextCursor);
      if (generation !== requestGenerationRef.current) return;
      setAdminMemories((current) => {
        const byId = new Map(current.map((memory) => [memory.id, memory]));
        page.items.forEach((memory) => byId.set(memory.id, memory));
        return Array.from(byId.values());
      });
      setAdminMemoryTotal(page.total);
      setAdminNextCursor(page.nextCursor);
    } catch (error) {
      if (generation !== requestGenerationRef.current) return;
      setMessage(error instanceof Error ? error.message : "More memories could not be loaded");
    } finally {
      if (generation === requestGenerationRef.current) setLoadingMore(false);
    }
  };

  const forgetSelectedMemory = async () => {
    if (!selectedMemory || deleting) return;
    if (selectedMemory.legalHold) {
      setMessage("This memory is protected by a legal hold");
      return;
    }
    if (!window.confirm("Forget this memory? Its source conversation will remain available.")) return;
    const generation = requestGenerationRef.current;
    setDeleting(true);
    try {
      await deleteMemory(selectedMemory.entityId, agentId);
      if (generation !== requestGenerationRef.current) return;
      setMessage("Memory deleted");
      setDetailOpen(false);
      await refreshData();
    } catch (error) {
      if (generation !== requestGenerationRef.current) return;
      setMessage(error instanceof Error ? error.message : "Memory could not be deleted");
    } finally {
      if (activeAgentRef.current === agentId) setDeleting(false);
    }
  };

  const executeRetention = async () => {
    const policy = selectedSettings?.policy;
    if (runningRetention || !capabilities?.available || !policy?.enabled) return;
    if (!window.confirm(
      "Run retention for all users of this service instance? Eligible memories will be marked and deleted; current legal holds will be respected.",
    )) return;
    const generation = requestGenerationRef.current;
    setRunningRetention(true);
    setMessage("Running retention...");
    try {
      const report = await runRetention(policy.policyId);
      if (generation !== requestGenerationRef.current) return;
      await refreshData();
      if (activeAgentRef.current !== agentId) return;
      setSelectedRunId(report.runId ?? "");
      setMessage("Retention finished. Review marked memories and committed outcomes in Activity.");
    } catch (error) {
      if (generation !== requestGenerationRef.current) return;
      setMessage(error instanceof Error ? error.message : "Retention could not be completed");
    } finally {
      if (activeAgentRef.current === agentId) setRunningRetention(false);
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
              <strong>Service instance / Memory administration</strong>
              <span>Administrator controls</span>
            </div>
            <div className="memory-workspace__context-actions">
              <Button kind="ghost" size="sm" onClick={onClose}>Back to chat</Button>
              <Button kind="secondary" size="sm" onClick={() => setView("user")}>Your memory</Button>
            </div>
          </div>

          <div className="memory-workspace__tabs" role="tablist" aria-label="Memory administration">
            {(["settings", "memory", "activity"] as AdminTab[]).map((tab) => (
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

          {adminTab === "settings" && (
            <div
              role="tabpanel"
              aria-label="Settings"
              className="memory-workspace__settings"
            >
              <div className="memory-settings__toolbar">
                <Button
                  kind="ghost"
                  size="sm"
                  renderIcon={Renew}
                  disabled={loading}
                  onClick={() => void refreshData()}
                >
                  Refresh
                </Button>
              </div>
              <Grid fullWidth>
                <Column sm={4} md={8} lg={16}>
                  <TabsVertical
                    selectedIndex={settingsIndex}
                    onChange={({ selectedIndex }) =>
                      setSelectedSettingsId(
                        settingsCategories[selectedIndex].id,
                      )
                    }
                  >
                    <TabListVertical aria-label="Settings categories">
                      {settingsCategories.map((item) => (
                        <Tab key={item.id}>{item.title}</Tab>
                      ))}
                    </TabListVertical>
                    <TabPanels>
                      {settingsCategories.map((category) => (
                        <TabPanel
                          key={category.id}
                          className="memory-settings__panel"
                        >
                          {category.id === "filters" ? (
                            <>
                              <p className="memory-settings__intro">
                                Control what enters memory and what reaches the
                                AI model.
                              </p>
                              <p className="memory-settings__note">
                                All users of this service instance · Managed by
                                the service operator
                              </p>
                              {settingsItems
                                .filter((item) => item.kind === "protection")
                                .map((item) => (
                                  <SettingsDetail
                                    key={item.id}
                                    settings={item}
                                    capabilities={capabilities}
                                    runningRetention={runningRetention}
                                    onRunRetention={() => {}}
                                  />
                                ))}
                              <p className="memory-settings__note">
                                This page reports the active configuration.
                                Filter changes are managed by your service
                                operator.
                              </p>
                            </>
                          ) : (
                            <SettingsDetail
                              settings={
                                settingsItems.find(
                                  (item) => item.id === category.id,
                                )!
                              }
                              capabilities={capabilities}
                              latestRun={runs.find(
                                (run) =>
                                  run.policyId ===
                                  settingsItems.find(
                                    (item) => item.id === category.id,
                                  )?.policy?.policyId,
                              )}
                              runningRetention={runningRetention}
                              onRunRetention={() => void executeRetention()}
                            />
                          )}
                        </TabPanel>
                      ))}
                    </TabPanels>
                  </TabsVertical>
                </Column>
              </Grid>
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
                  <p>Review retention outcomes, policy attribution, and administrative audit records.</p>
                </Column>
              </Grid>
              <CollectionActivity memories={adminMemories} refreshKey={runs} onOpen={(id) => {
                setAdminOwner("all"); setAdminState("all");
                setSelectedAdminMemoryId(id); setAdminTab("memory"); setDetailOpen(true);
              }} />
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
                      setAdminOwner("all");
                      setAdminState("all");
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
