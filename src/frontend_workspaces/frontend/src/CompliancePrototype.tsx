import React, { useMemo, useState } from "react";
import {
  Button,
  Column,
  ComposedModal,
  Grid,
  ModalBody,
  ModalHeader,
  Search,
  Select,
  SelectItem,
  TextInput,
  Toggle,
} from "@carbon/react";
import { ArrowRight, Close } from "@carbon/icons-react";
import {
  createComplianceDemoData,
  type ActivityRecord,
  type AutomationRecord,
  type DeliveryRecord,
  type MemoryRecord,
  type MemoryState,
} from "./CompliancePrototypeData";
import {
  applyRetentionReport,
  deleteLiveMemory,
  loadLiveComplianceData,
  runRetentionPreview,
  saveAutomationConfig,
  type RetentionTransparency,
} from "./CompliancePrototypeApi";
import "./CompliancePrototype.scss";

type AdminTab = "automation" | "memory" | "activity";
type MemorySort =
  | "recently-saved"
  | "recently-used"
  | "most-used"
  | "least-used"
  | "oldest"
  | "name";
type DetailSheet =
  | "user-memory"
  | "automation"
  | "latest-activity"
  | "delivery"
  | "admin-memory"
  | "activity"
  | null;

type CompliancePrototypeProps = {
  onClose?: () => void;
  canManage?: boolean;
  embedded?: boolean;
  agentName?: string;
  focusEntityIds?: string[];
  focusRelationship?: "used" | "saved";
  onClearFocus?: () => void;
  onOpenConversation?: (threadId: string) => void;
};

const USER_CATEGORY_ORDER = [
  "Work context",
  "Preference",
  "Customer fact",
  "Guidance",
  "Conversation",
] as const;

const ATTENTION_STATES: MemoryState[] = [
  "Needs attention",
  "Scheduled for deletion",
  "Protected",
];

function createEmptyComplianceData(agentName: string) {
  const templates = createComplianceDemoData(agentName);
  return {
    ...templates,
    automations: templates.automations.map(
      (automation): AutomationRecord => ({
        ...automation,
        enabled: false,
        runtime: "unavailable",
        health: "Status unavailable",
        latest: "Live status unavailable",
      }),
    ),
    memories: [],
    activities: [],
    deliveries: [],
  };
}

function statusTone(status: string) {
  if (
    status === "Attention" ||
    status === "Warning" ||
    status === "Incomplete" ||
    status === "Needs attention" ||
    status === "Protected" ||
    status === "Scheduled for deletion"
  ) {
    return "warning";
  }
  if (status === "Deleted") {
    return "error";
  }
  return "healthy";
}

function protectionSummary(automation: AutomationRecord) {
  const action =
    automation.id === "save-check"
      ? "Filtering sensitive information before saving"
      : "Filtering sensitive information before sending";
  if (automation.runtime === "unavailable") {
    return { symbol: "?", text: `${action}: status unavailable`, tone: "neutral" };
  }
  if (!automation.enabled) {
    return { symbol: "!", text: `${action}: off`, tone: "warning" };
  }
  if (automation.health !== "Healthy") {
    return { symbol: "!", text: `${action}: needs attention`, tone: "warning" };
  }
  return { symbol: "✓", text: action, tone: "healthy" };
}

function recordDomId(scope: string, id: string) {
  return `compliance-record-${scope}-${id.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

function DefinitionList({
  items,
}: {
  items: Array<{ label: string; value: React.ReactNode }>;
}) {
  return (
    <dl className="compliance-d-definition-list">
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
      className="compliance-d-reference-link"
      href={href}
      onClick={(event) => {
        event.preventDefault();
        onClick();
      }}
    >
      <span>{children}</span>
      <ArrowRight size={16} aria-hidden="true" />
    </a>
  );
}

function RecordRow({
  recordId,
  scope,
  selected,
  title,
  meta,
  status,
  statusDetail,
  onSelect,
}: {
  recordId: string;
  scope: string;
  selected: boolean;
  title: string;
  meta: string;
  status?: string;
  statusDetail?: string;
  onSelect: () => void;
}) {
  return (
    <button
      id={recordDomId(scope, recordId)}
      type="button"
      className="compliance-d-record-row"
      aria-current={selected}
      onClick={onSelect}
    >
      <span className="compliance-d-record-copy">
        <strong>{title}</strong>
        <span>{meta}</span>
      </span>
      {status && (
        <span className={`compliance-d-record-state compliance-d-state--${statusTone(status)}`}>
          <strong>{status}</strong>
          {statusDetail && <small>{statusDetail}</small>}
        </span>
      )}
      <ArrowRight size={20} aria-hidden="true" />
    </button>
  );
}

function DetailPane({
  id,
  open,
  label,
  onClose,
  children,
}: {
  id: string;
  open: boolean;
  label: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <aside
        id={id}
        className="compliance-d-detail-pane"
        data-open={open}
        aria-label={label}
      >
        <div className="compliance-d-detail-close">
          <Button
            kind="ghost"
            size="sm"
            hasIconOnly
            renderIcon={Close}
            iconDescription={`Close ${label.toLowerCase()}`}
            onClick={onClose}
          />
        </div>
        {children}
      </aside>
      <button
        className="compliance-d-scrim"
        type="button"
        aria-label={`Close ${label.toLowerCase()}`}
        onClick={onClose}
      />
    </>
  );
}

function MasterDetail({
  listLabel,
  list,
  detail,
  detailId,
  detailLabel,
  sheetOpen,
  closeSheet,
}: {
  listLabel: string;
  list: React.ReactNode;
  detail: React.ReactNode;
  detailId: string;
  detailLabel: string;
  sheetOpen: boolean;
  closeSheet: () => void;
}) {
  return (
    <Grid condensed className="compliance-d-master-detail">
      <Column sm={4} md={8} lg={10} className="compliance-d-record-list">
        <section aria-label={listLabel}>{list}</section>
      </Column>
      <Column sm={4} md={8} lg={6} className="compliance-d-detail-column">
        <DetailPane
          id={detailId}
          open={sheetOpen}
          label={detailLabel}
          onClose={closeSheet}
        >
          {detail}
        </DetailPane>
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
    <div className="compliance-d-detail-head">
      <p className="compliance-d-eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      {status && (
        <div className={`compliance-d-detail-status compliance-d-state--${statusTone(status)}`}>
          {status}
        </div>
      )}
    </div>
  );
}

function MemoryDetail({
  memory,
  admin,
  onForget,
  onOpenConversation,
}: {
  memory: MemoryRecord;
  admin: boolean;
  onForget?: () => void;
  onOpenConversation?: (threadId: string) => void;
}) {
  const source =
    memory.sourceConversationId && onOpenConversation ? (
      <ReferenceLink
        href={`/chat?thread_id=${encodeURIComponent(memory.sourceConversationId)}`}
        onClick={() => onOpenConversation(memory.sourceConversationId!)}
      >
        {memory.sourceLabel}
      </ReferenceLink>
    ) : (
      memory.sourceLabel
    );

  return (
    <>
      <DetailHeader
        eyebrow={admin ? "Lifecycle detail" : "Selected memory"}
        title={memory.title}
        status={memory.state === "Retained" ? "Current" : memory.state}
      />
      <div className="compliance-d-detail-body">
        {!admin && <p>{memory.why}</p>}
        {!admin && memory.rememberedContent && (
          <div className="compliance-d-notice">
            <strong>Remembered information</strong>
            <p>{memory.rememberedContent}</p>
          </div>
        )}
        <DefinitionList
          items={[
            ...(admin ? [{ label: "Person", value: memory.person }] : []),
            { label: "Category", value: memory.category },
            { label: "Source", value: source },
            {
              label: "Use frequency",
              value: `Used ${memory.usageCount ?? 0} ${
                (memory.usageCount ?? 0) === 1 ? "time" : "times"
              }`,
            },
            { label: "Last used", value: memory.lastUsedLabel },
            {
              label: admin ? "Outcome" : "Lifecycle",
              value: admin ? memory.state : memory.lifecycle,
            },
            { label: "Reason", value: memory.statusDetail },
            {
              label: "Related",
              value: memory.relatedIds.length
                ? `${memory.relatedIds.length} related ${memory.relatedIds.length === 1 ? "memory" : "memories"}`
                : "No linked memories",
            },
          ]}
        />
        {(memory.recentUsage?.length ?? 0) > 0 && (
          <section className="compliance-d-recent-usage">
            <h3>Recent use</h3>
            <ul>
              {memory.recentUsage!.map((usage, index) => (
                <li key={`${usage.threadId}-${usage.usedAt}-${index}`}>
                  {usage.threadId && onOpenConversation ? (
                    <ReferenceLink
                      href={`/chat?thread_id=${encodeURIComponent(usage.threadId)}`}
                      onClick={() => onOpenConversation(usage.threadId)}
                    >
                      {usage.conversationLabel}
                    </ReferenceLink>
                  ) : (
                    usage.conversationLabel
                  )}
                  <span>{usage.usedLabel}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
        {admin ? (
          <div className="compliance-d-notice">
            <strong>Content hidden</strong>
            <p>Viewing stored content requires separate permission.</p>
          </div>
        ) : (
          <>
            {memory.legalHold && (
              <div className="compliance-d-notice">
                <strong>Deletion is unavailable</strong>
                <p>This memory is protected by a legal hold.</p>
              </div>
            )}
            <div className="compliance-d-detail-actions">
              <Button kind="danger" size="sm" disabled={memory.legalHold} onClick={onForget}>
              Forget
              </Button>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function ActivityDetail({
  activity,
  memories,
  showFullRecord,
  onOpenActivity,
  onOpenMemory,
}: {
  activity: ActivityRecord;
  memories: MemoryRecord[];
  showFullRecord: boolean;
  onOpenActivity: (id: string) => void;
  onOpenMemory: (id: string) => void;
}) {
  const affected = activity.affectedMemoryIds
    .map((id) => memories.find((memory) => memory.id === id))
    .filter((memory): memory is MemoryRecord => Boolean(memory));

  return (
    <>
      <DetailHeader
        eyebrow={`${activity.type} / ${activity.timestamp}`}
        title={activity.title}
        status={activity.status}
      />
      <div className="compliance-d-detail-body">
        <p>{activity.summary}</p>
        {activity.notice && (
          <div className="compliance-d-notice">
            <strong>{activity.notice.title}</strong>
            <p>{activity.notice.text}</p>
          </div>
        )}
        <DefinitionList
          items={[
            ...activity.facts,
            ...(showFullRecord
              ? [
                  {
                    label: "Activity",
                    value: (
                      <ReferenceLink
                        href={`/chat?memory_activity=${encodeURIComponent(activity.id)}`}
                        onClick={() => onOpenActivity(activity.id)}
                      >
                        Full activity record
                      </ReferenceLink>
                    ),
                  },
                ]
              : []),
          ]}
        />
        {affected.length > 0 && (
          <section className="compliance-d-references" aria-labelledby={`${activity.id}-affected`}>
            <h3 id={`${activity.id}-affected`}>Affected memory</h3>
            <ul>
              {affected.map((memory) => (
                <li key={memory.id}>
                  <ReferenceLink
                    href={`/chat?memory_id=${encodeURIComponent(memory.entityId)}`}
                    onClick={() => onOpenMemory(memory.id)}
                  >
                    <span>
                      <strong>{memory.title}</strong>
                      <small>{memory.statusDetail}</small>
                    </span>
                  </ReferenceLink>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}

function DeliveryDetail({
  delivery,
  memories,
  onOpenActivity,
  onOpenMemory,
}: {
  delivery: DeliveryRecord;
  memories: MemoryRecord[];
  onOpenActivity: (id: string) => void;
  onOpenMemory: (id: string) => void;
}) {
  const affectedMemory = delivery.affectedMemoryId
    ? memories.find((memory) => memory.id === delivery.affectedMemoryId)
    : undefined;

  return (
    <>
      <DetailHeader
        eyebrow="Simulated lifecycle notification"
        title={delivery.title}
        status="Recorded locally"
      />
      <div className="compliance-d-detail-body">
        <p>
          This is the notification CUGA would publish for this memory outcome
          after an eventing destination is connected.
        </p>
        <DefinitionList
          items={[
            { label: "Outcome", value: delivery.outcomeLabel ?? "Retention outcome" },
            { label: "Recorded", value: delivery.deliveredAt },
            {
              label: "Destination",
              value: "Local PoC ledger; no external destination is connected",
            },
            {
              label: "Triggered by",
              value: (
                <ReferenceLink
                  href={`/chat?memory_activity=${encodeURIComponent(delivery.relatedActivityId)}`}
                  onClick={() => onOpenActivity(delivery.relatedActivityId)}
                >
                  {delivery.relatedActivityTitle ?? "Retention simulation"}
                </ReferenceLink>
              ),
            },
            ...(delivery.affectedMemoryId
              ? [
                  {
                    label: "Memory",
                    value: affectedMemory ? (
                      <ReferenceLink
                        href={`/chat?memory_id=${encodeURIComponent(affectedMemory.entityId)}`}
                        onClick={() => onOpenMemory(affectedMemory.id)}
                      >
                        {affectedMemory.title}
                      </ReferenceLink>
                    ) : "Memory is no longer available",
                  },
                ]
              : []),
            {
              label: "Contents",
              value:
                "Lifecycle references only. The remembered information is not included.",
            },
          ]}
        />
      </div>
    </>
  );
}

function AutomationDetail({
  automation,
  editing,
  runningRetention,
  onEdit,
  onCancel,
  onSave,
  onRunRetention,
}: {
  automation: AutomationRecord;
  editing: boolean;
  runningRetention: boolean;
  onEdit: () => void;
  onCancel: () => void;
  onSave: (record: AutomationRecord) => void;
  onRunRetention: () => void;
}) {
  const [draft, setDraft] = useState(automation);

  React.useEffect(() => {
    setDraft(automation);
  }, [automation, editing]);

  if (editing) {
    return (
      <>
        <DetailHeader eyebrow="Edit automation" title={automation.title} />
        <form
          className="compliance-d-detail-body compliance-d-edit-form"
          onSubmit={(event) => {
            event.preventDefault();
            const runtime =
              draft.kind === "protection"
                ? {
                    runtime: "active" as const,
                    health: draft.enabled ? ("Healthy" as const) : ("Not running" as const),
                  }
                : {
                    runtime: "configured" as const,
                    health: "Configured only" as const,
                  };
            onSave({
              ...draft,
              ...runtime,
              schedule:
                draft.kind === "retention"
                  ? `${draft.frequency} at ${draft.time}`
                  : draft.schedule,
            });
          }}
        >
          <Toggle
            id={`automation-enabled-${automation.id}`}
            size="sm"
            labelText="Enabled"
            labelA="Off"
            labelB="On"
            toggled={draft.enabled}
            onToggle={(enabled) => setDraft((current) => ({ ...current, enabled }))}
          />
          {draft.kind === "retention" && (
            <>
              <Select
                id="automation-frequency"
                labelText="Run frequency"
                value={draft.frequency}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    frequency: event.target.value as AutomationRecord["frequency"],
                  }))
                }
              >
                <SelectItem value="Every day" text="Every day" />
                <SelectItem value="Every week" text="Every week" />
                <SelectItem value="Every month" text="Every month" />
              </Select>
              <TextInput
                id="automation-time"
                type="time"
                labelText="Run time"
                value={draft.time}
                onChange={(event) =>
                  setDraft((current) => ({ ...current, time: event.target.value }))
                }
              />
            </>
          )}
          {draft.kind === "events" && (
            <TextInput
              id="automation-destination"
              labelText="Destination"
              value={draft.destination}
              onChange={(event) =>
                setDraft((current) => ({
                  ...current,
                  destination: event.target.value,
                }))
              }
            />
          )}
          <div className="compliance-d-detail-actions">
            <Button type="button" kind="secondary" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button type="submit" size="sm">
              Save
            </Button>
          </div>
        </form>
      </>
    );
  }

  return (
    <>
      <DetailHeader
        eyebrow="Automation"
        title={automation.title}
        status={automation.enabled ? `Enabled and ${automation.health.toLowerCase()}` : "Disabled"}
      />
      <div className="compliance-d-detail-body">
        <p>{automation.description}</p>
        <DefinitionList
          items={[
            { label: "Status", value: automation.enabled ? "Enabled" : "Disabled" },
            { label: "Health", value: automation.health },
            { label: "Schedule", value: automation.schedule },
            { label: "Latest", value: automation.latest },
            ...(automation.destination
              ? [{ label: "Destination", value: automation.destination }]
              : []),
          ]}
        />
        {automation.kind === "protection" && (
          <div className="compliance-d-proposal">
            <strong>Reported from Evolve</strong>
            <p>
              This status is read from the Evolve hook configuration. CUGA does
              not edit protection plugins from this screen.
            </p>
          </div>
        )}
        <div className="compliance-d-detail-actions">
          {automation.kind === "retention" && (
            <Button
              kind="ghost"
              size="sm"
              disabled={runningRetention}
              onClick={onRunRetention}
            >
              {runningRetention ? "Running preview..." : "Run preview"}
            </Button>
          )}
          {automation.kind !== "protection" && (
            <Button kind="secondary" size="sm" onClick={onEdit}>Edit</Button>
          )}
        </div>
      </div>
    </>
  );
}

export function CompliancePrototype({
  onClose,
  canManage = true,
  embedded = false,
  agentName = "ACME Support",
  focusEntityIds = [],
  focusRelationship = "used",
  onClearFocus,
  onOpenConversation,
}: CompliancePrototypeProps) {
  const rootRef = React.useRef<HTMLElement>(null);
  const [data, setData] = useState(() => createEmptyComplianceData(agentName));
  const [view, setView] = useState<"user" | "admin">("user");
  const [adminTab, setAdminTab] = useState<AdminTab>("automation");
  const [detailSheet, setDetailSheet] = useState<DetailSheet>(null);
  const [message, setMessage] = useState("");
  const [userSearch, setUserSearch] = useState("");
  const [userCategory, setUserCategory] = useState("all");
  const [userSort, setUserSort] = useState<MemorySort>("recently-saved");
  const [selectedUserMemoryId, setSelectedUserMemoryId] = useState("memory-6");
  const [selectedAdminMemoryId, setSelectedAdminMemoryId] = useState("memory-3");
  const [selectedAutomationId, setSelectedAutomationId] = useState("save-check");
  const [editingAutomation, setEditingAutomation] = useState(false);
  const [selectedLatestActivityId, setSelectedLatestActivityId] = useState("R-240724-0200");
  const [selectedActivityId, setSelectedActivityId] = useState("R-240724-0200");
  const [selectedDeliveryId, setSelectedDeliveryId] = useState("EVT-1042");
  const [adminPerson, setAdminPerson] = useState("all");
  const [adminMemoryState, setAdminMemoryState] = useState("all");
  const [activityType, setActivityType] = useState("all");
  const [liveData, setLiveData] = useState(false);
  const [liveUserMemories, setLiveUserMemories] = useState<MemoryRecord[]>([]);
  const [retention, setRetention] = useState<RetentionTransparency | null>(null);
  const [runningRetention, setRunningRetention] = useState(false);
  const deepLinkApplied = React.useRef(false);

  React.useEffect(() => {
    const scrollContainer =
      rootRef.current?.closest(".cds--modal-content") ?? rootRef.current;
    scrollContainer?.scrollTo({ top: 0, behavior: "instant" });
  }, [adminTab, view]);

  const loadMemoryData = React.useCallback(async () => {
    const templates = createEmptyComplianceData(agentName);
    try {
      const loaded = await loadLiveComplianceData(templates, canManage);
      setData(loaded.data);
      setLiveUserMemories(loaded.userMemories);
      setRetention(loaded.retention);
      setLiveData(true);
      setSelectedUserMemoryId((current) =>
        loaded.userMemories.some((memory) => memory.id === current)
          ? current
          : loaded.userMemories[0]?.id ?? "",
      );
      setSelectedAdminMemoryId((current) =>
        loaded.data.memories.some((memory) => memory.id === current)
          ? current
          : loaded.data.memories[0]?.id ?? "",
      );
      setSelectedLatestActivityId((current) =>
        loaded.data.activities.some((activity) => activity.id === current) ? current : "",
      );
      setSelectedActivityId((current) =>
        loaded.data.activities.some((activity) => activity.id === current) ? current : "",
      );
      setSelectedDeliveryId((current) =>
        loaded.data.deliveries.some((delivery) => delivery.eventId === current) ? current : "",
      );
    } catch {
      setMessage("Evolve is unavailable; no memory data was substituted");
    }
  }, [agentName, canManage]);

  React.useEffect(() => {
    let active = true;
    const initialize = async () => {
      if (active) {
        await loadMemoryData();
      }
    };
    void initialize();

    const refresh = () => {
      if (document.visibilityState === "visible" && !editingAutomation) {
        void loadMemoryData();
      }
    };
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    const interval = window.setInterval(refresh, 30_000);
    return () => {
      active = false;
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
      window.clearInterval(interval);
    };
  }, [editingAutomation, loadMemoryData]);

  React.useEffect(() => {
    if (deepLinkApplied.current || !liveData) return;
    deepLinkApplied.current = true;
    const params = new URLSearchParams(window.location.search);
    const memoryId = params.get("memory_id");
    const activityId = params.get("memory_activity");
    if (memoryId) {
      setView("admin");
      setAdminTab("memory");
      setSelectedAdminMemoryId(`memory-${memoryId}`);
      setDetailSheet("admin-memory");
    } else if (activityId) {
      setView("admin");
      setAdminTab("activity");
      setSelectedActivityId(activityId);
      setDetailSheet("activity");
    }
  }, [liveData]);

  const userMemories = useMemo(
    () => {
      const filtered = (liveData ? liveUserMemories : data.memories).filter((memory) => {
        const query = userSearch.trim().toLowerCase();
        return (
          (liveData || memory.person === "Dana Whitfield") &&
          (!focusEntityIds.length || focusEntityIds.includes(memory.entityId)) &&
          (userCategory === "all" || memory.category === userCategory) &&
          (!query ||
            `${memory.title} ${memory.category} ${memory.sourceLabel}`
              .toLowerCase()
              .includes(query))
        );
      });
      const lastUsed = (memory: MemoryRecord) =>
        Date.parse(memory.recentUsage?.[0]?.usedAt ?? "") || 0;
      return [...filtered].sort((left, right) => {
        if (userSort === "recently-used") return lastUsed(right) - lastUsed(left);
        if (userSort === "most-used") return (right.usageCount ?? 0) - (left.usageCount ?? 0);
        if (userSort === "least-used") return (left.usageCount ?? 0) - (right.usageCount ?? 0);
        if (userSort === "oldest") return right.createdDaysAgo - left.createdDaysAgo;
        if (userSort === "name") return left.title.localeCompare(right.title);
        return left.createdDaysAgo - right.createdDaysAgo;
      });
    },
    [
      data.memories,
      focusEntityIds,
      liveData,
      liveUserMemories,
      userCategory,
      userSearch,
      userSort,
    ],
  );

  const adminMemories = useMemo(
    () =>
      data.memories.filter(
        (memory) =>
          (adminPerson === "all" || memory.person === adminPerson) &&
          (adminMemoryState === "all" ||
            (adminMemoryState === "attention"
              ? ATTENTION_STATES.includes(memory.state)
              : memory.state === adminMemoryState)),
      ),
    [adminMemoryState, adminPerson, data.memories],
  );

  const filteredActivities = useMemo(
    () =>
      data.activities.filter(
        (activity) => activityType === "all" || activity.type === activityType,
      ),
    [activityType, data.activities],
  );

  React.useEffect(() => {
    if (!userMemories.some((memory) => memory.id === selectedUserMemoryId)) {
      setSelectedUserMemoryId(userMemories[0]?.id ?? "");
    }
  }, [selectedUserMemoryId, userMemories]);

  React.useEffect(() => {
    if (!adminMemories.some((memory) => memory.id === selectedAdminMemoryId)) {
      setSelectedAdminMemoryId(adminMemories[0]?.id ?? "");
    }
  }, [adminMemories, selectedAdminMemoryId]);

  React.useEffect(() => {
    if (!filteredActivities.some((activity) => activity.id === selectedActivityId)) {
      setSelectedActivityId(filteredActivities[0]?.id ?? "");
    }
  }, [filteredActivities, selectedActivityId]);

  const selectedUserMemory =
    userMemories.find((memory) => memory.id === selectedUserMemoryId) ??
    userMemories[0];
  const selectedAdminMemory =
    adminMemories.find((memory) => memory.id === selectedAdminMemoryId) ??
    adminMemories[0];
  const selectedAutomation =
    data.automations.find((automation) => automation.id === selectedAutomationId) ??
    data.automations[0];
  const selectedLatestActivity =
    data.activities.find((activity) => activity.id === selectedLatestActivityId) ??
    data.activities[0];
  const selectedActivity =
    data.activities.find((activity) => activity.id === selectedActivityId) ??
    data.activities[0];
  const selectedDelivery =
    data.deliveries.find((delivery) => delivery.eventId === selectedDeliveryId) ??
    data.deliveries[0];
  const userMemoryTotal = liveData
    ? liveUserMemories.length
    : data.memories.filter((memory) => memory.person === "Dana Whitfield").length;
  const adminPeople = Array.from(new Set(data.memories.map((memory) => memory.person))).sort();
  const sourceConversationCount = new Set(
    data.memories
      .map((memory) => memory.sourceConversationId)
      .filter((id): id is string => Boolean(id)),
  ).size;

  const scrollRecordIntoView = React.useCallback((scope: string, id: string) => {
    window.requestAnimationFrame(() => {
      rootRef.current
        ?.querySelector<HTMLElement>(`#${recordDomId(scope, id)}`)
        ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }, []);

  React.useEffect(() => {
    if (view === "user" && selectedUserMemoryId) {
      scrollRecordIntoView("user-memory", selectedUserMemoryId);
    } else if (view === "admin" && adminTab === "memory" && selectedAdminMemoryId) {
      scrollRecordIntoView("admin-memory", selectedAdminMemoryId);
    } else if (view === "admin" && adminTab === "activity" && selectedActivityId) {
      scrollRecordIntoView("activity", selectedActivityId);
    }
  }, [
    adminTab,
    scrollRecordIntoView,
    selectedActivityId,
    selectedAdminMemoryId,
    selectedUserMemoryId,
    view,
  ]);

  React.useEffect(() => {
    if (view !== "admin" || adminTab !== "automation") return;
    if (detailSheet === "automation") {
      scrollRecordIntoView("automation", selectedAutomationId);
    } else if (detailSheet === "latest-activity" && selectedLatestActivityId) {
      scrollRecordIntoView("latest-activity", selectedLatestActivityId);
    } else if (detailSheet === "delivery" && selectedDeliveryId) {
      scrollRecordIntoView("delivery", selectedDeliveryId);
    }
  }, [
    adminTab,
    detailSheet,
    scrollRecordIntoView,
    selectedAutomationId,
    selectedDeliveryId,
    selectedLatestActivityId,
    view,
  ]);

  const openAdminMemory = (id: string) => {
    setView("admin");
    setAdminTab("memory");
    setSelectedAdminMemoryId(id);
    setDetailSheet("admin-memory");
  };

  const openActivity = (id: string) => {
    setView("admin");
    setAdminTab("activity");
    setSelectedActivityId(id);
    setDetailSheet("activity");
  };

  const handleForgetMemory = async () => {
    if (!selectedUserMemory) {
      return;
    }
    if (selectedUserMemory.legalHold) {
      setMessage("This memory cannot be deleted while its legal hold is active");
      return;
    }
    if (liveData) {
      if (!window.confirm(`Forget this memory? Its source conversation will remain available.`)) return;
      try {
        await deleteLiveMemory(selectedUserMemory.entityId);
        setMessage(`Forgot ${selectedUserMemory.title}`);
        await loadMemoryData();
      } catch {
        setMessage("The memory could not be deleted; no local change was made");
      }
      return;
    }
    setMessage("Memory deletion is unavailable while Evolve is offline");
  };

  const selectAdminTab = (tab: AdminTab) => {
    setAdminTab(tab);
    setDetailSheet(null);
  };

  const renderMemoryRows = (memories: MemoryRecord[], selectedId: string, admin: boolean) => (
    <ul className="compliance-d-list">
      {memories.map((memory) => (
        <li key={memory.id}>
          <RecordRow
            recordId={memory.id}
            scope={admin ? "admin-memory" : "user-memory"}
            selected={selectedId === memory.id}
            title={memory.title}
            meta={
              admin
                ? `${memory.person} / ${memory.sourceLabel} / Used ${memory.usageCount ?? 0} times`
                : `${memory.sourceLabel} / Used ${memory.usageCount ?? 0} ${
                    (memory.usageCount ?? 0) === 1 ? "time" : "times"
                  }`
            }
            status={memory.state === "Retained" ? "Current" : memory.state}
            statusDetail={admin ? memory.statusDetail : memory.category}
            onSelect={() => {
              if (admin) {
                setSelectedAdminMemoryId(memory.id);
                setDetailSheet("admin-memory");
              } else {
                setSelectedUserMemoryId(memory.id);
                setDetailSheet("user-memory");
              }
            }}
          />
        </li>
      ))}
    </ul>
  );

  const renderUserMemoryList = () => (
    <div>
      <div className="compliance-d-list-head">
        <h2>What the agent remembers</h2>
        <p>Select a memory to inspect its source and controls.</p>
        {focusEntityIds.length > 0 && (
          <Button kind="ghost" size="sm" onClick={onClearFocus}>
            Showing {focusEntityIds.length} {focusRelationship} in the response. Show all
          </Button>
        )}
      </div>
      {renderMemoryRows(userMemories, selectedUserMemory?.id ?? "", false)}
      {!userMemories.length && (
        <p className="compliance-d-empty">No memories match these filters.</p>
      )}
    </div>
  );

  const content = (
    <main
      ref={rootRef}
      className={`compliance-d${embedded ? " compliance-d--embedded" : ""}`}
    >
      {message && (
        <div className="compliance-d-demo-bar">
          <span role="status" aria-live="polite">
            {message}
          </span>
        </div>
      )}

      {view === "user" ? (
        <>
          <div className="compliance-d-context-bar">
            <div>
              <strong>{agentName}</strong>
              <span>Your view of this agent&apos;s memory</span>
            </div>
            <div className="compliance-d-context-actions">
              {canManage && (
                <Button
                  kind="secondary"
                  size="sm"
                  renderIcon={ArrowRight}
                  onClick={() => {
                    setView("admin");
                    setAdminTab("automation");
                    setDetailSheet(null);
                  }}
                >
                  Manage agent
                </Button>
              )}
            </div>
          </div>

          <Grid className="compliance-d-page-head">
            <Column sm={4} md={5} lg={11} className="compliance-d-page-copy">
              <p className="compliance-d-eyebrow">Your memory</p>
              <h1>Memory</h1>
              <p>
                Review what {agentName} remembers about you and delete memories
                that are no longer useful.
              </p>
            </Column>
            <Column sm={4} md={3} lg={5} className="compliance-d-summary">
              <strong>{userMemoryTotal}</strong>
              <span>memories about you</span>
              {data.automations
                .filter((automation) => automation.kind === "protection")
                .map((automation) => {
                  const summary = protectionSummary(automation);
                  return (
                    <p key={automation.id}>
                      <span
                        className={`compliance-d-status-marker compliance-d-status-marker--${summary.tone}`}
                        aria-hidden="true"
                      >
                        {summary.symbol}
                      </span>{" "}
                      {summary.text}
                    </p>
                  );
                })}
              {retention && (
                <div className="compliance-d-retention-summary">
                  <strong>Retention policy</strong>
                  <ul>
                    {retention.rules.map((rule) => (
                      <li key={rule.summary}>
                        <span
                          className={`compliance-d-status-marker compliance-d-status-marker--${
                            rule.scheduled ? "healthy" : "error"
                          }`}
                          aria-hidden="true"
                        >
                          {rule.scheduled ? "✓" : "×"}
                        </span>{" "}
                        {rule.summary}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Column>
          </Grid>

          <Grid className="compliance-d-toolbar">
            <Column sm={4} md={4} lg={5}>
              <Search
                id="user-memory-search"
                size="lg"
                labelText="Search your memories"
                placeholder={`Search ${userMemoryTotal} memories`}
                value={userSearch}
                onChange={(event) => setUserSearch(event.target.value)}
              />
            </Column>
            <Column sm={4} md={2} lg={3}>
              <Select
                id="user-memory-category"
                labelText="Category"
                value={userCategory}
                onChange={(event) => setUserCategory(event.target.value)}
              >
                <SelectItem value="all" text="All categories" />
                {USER_CATEGORY_ORDER.map((category) => (
                  <SelectItem key={category} value={category} text={category} />
                ))}
              </Select>
            </Column>
            <Column sm={4} md={2} lg={4}>
              <Select
                id="user-memory-sort"
                labelText="Sort"
                value={userSort}
                onChange={(event) => {
                  setUserSort(event.target.value as MemorySort);
                  setSelectedUserMemoryId("");
                  setDetailSheet(null);
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
            <Column sm={4} md={8} lg={4} className="compliance-d-toolbar-summary">
              Showing {userMemories.length} of{" "}
              {userMemoryTotal}
            </Column>
          </Grid>

          <MasterDetail
            listLabel="Your memories"
            list={renderUserMemoryList()}
            detail={
              selectedUserMemory ? (
                <MemoryDetail
                  memory={selectedUserMemory}
                  admin={false}
                  onForget={handleForgetMemory}
                  onOpenConversation={onOpenConversation}
                />
              ) : (
                <p className="compliance-d-empty">Select a memory to view its details.</p>
              )
            }
            detailId="user-memory-detail"
            detailLabel="Memory details"
            sheetOpen={detailSheet === "user-memory"}
            closeSheet={() => setDetailSheet(null)}
          />
        </>
      ) : (
        <>
          <div className="compliance-d-admin-topline">
            <div>
              <strong>{agentName} / Compliance</strong>
              <span>Service-owner administration</span>
            </div>
            <div className="compliance-d-admin-actions">
              <Button
                kind="secondary"
                size="sm"
                onClick={() => {
                  setView("user");
                  setDetailSheet(null);
                }}
              >
                Your memory
              </Button>
            </div>
          </div>

          <div className="compliance-d-tabs" role="tablist" aria-label="Compliance administration">
            {(["automation", "memory", "activity"] as AdminTab[]).map((tab) => (
              <button
                key={tab}
                type="button"
                role="tab"
                aria-selected={adminTab === tab}
                onClick={() => selectAdminTab(tab)}
              >
                {tab[0].toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </div>

          {adminTab === "automation" && (
            <div role="tabpanel" aria-label="Automation">
              <Grid className="compliance-d-admin-page-head">
                <Column sm={4} md={8} lg={16}>
                  <p className="compliance-d-eyebrow">Published configuration</p>
                  <h1>Automation</h1>
                  <p>
                    Protection runs continuously. Retention and event delivery
                    are configured for this simulation; no scheduler or external
                    event transport is connected.
                  </p>
                </Column>
              </Grid>

              <section className="compliance-d-section-band">
                <div className="compliance-d-section-title">
                  <h2>Automation status</h2>
                  <p>Select an automation to inspect its health, schedule, and configuration.</p>
                </div>
                <MasterDetail
                  listLabel="Automation status"
                  list={
                    <ul className="compliance-d-list">
                      {data.automations.map((automation) => (
                        <li key={automation.id}>
                          <RecordRow
                            recordId={automation.id}
                            scope="automation"
                            selected={automation.id === selectedAutomation.id}
                            title={automation.title}
                            meta={automation.description}
                            status={
                              automation.enabled
                                ? `Enabled and ${automation.health.toLowerCase()}`
                                : "Disabled"
                            }
                            statusDetail={automation.schedule}
                            onSelect={() => {
                              setSelectedAutomationId(automation.id);
                              setEditingAutomation(false);
                              setDetailSheet("automation");
                            }}
                          />
                        </li>
                      ))}
                    </ul>
                  }
                  detail={
                    <AutomationDetail
                      key={selectedAutomation.id}
                      automation={selectedAutomation}
                      editing={editingAutomation}
                      runningRetention={runningRetention}
                      onEdit={() => setEditingAutomation(true)}
                      onCancel={() => setEditingAutomation(false)}
                      onRunRetention={async () => {
                        setRunningRetention(true);
                        setMessage("Simulating the configured retention run in Evolve...");
                        try {
                          const report = await runRetentionPreview();
                          await loadMemoryData();
                          setData((current) => applyRetentionReport(current, report));
                          setSelectedLatestActivityId(report.run_id);
                          setSelectedActivityId(report.run_id);
                          setMessage("Retention simulation completed");
                        } catch {
                          setMessage("Retention simulation could not reach Evolve");
                        } finally {
                          setRunningRetention(false);
                        }
                      }}
                      onSave={async (updated) => {
                        if (liveData) {
                          try {
                            await saveAutomationConfig(updated);
                          } catch {
                            setMessage("Automation settings could not be saved");
                            return;
                          }
                        }
                        setData((current) => ({
                          ...current,
                          automations: current.automations.map((automation) =>
                            automation.id === updated.id ? updated : automation,
                          ),
                        }));
                        setEditingAutomation(false);
                        setMessage(`${updated.title} updated`);
                      }}
                    />
                  }
                  detailId="automation-detail"
                  detailLabel="Automation details"
                  sheetOpen={detailSheet === "automation"}
                  closeSheet={() => setDetailSheet(null)}
                />
              </section>

              <section className="compliance-d-section-band">
                <div className="compliance-d-section-title">
                  <h2>Latest activity</h2>
                  <p>Results from manual simulations of the configured schedule.</p>
                </div>
                <MasterDetail
                  listLabel="Latest activity"
                  list={
                    <ul className="compliance-d-list">
                      {data.activities
                        .slice(0, 3)
                        .map((activity) => (
                          <li key={activity.id}>
                            <RecordRow
                              recordId={activity.id}
                              scope="latest-activity"
                              selected={activity.id === selectedLatestActivity?.id}
                              title={activity.title}
                              meta={activity.timestamp}
                              status={activity.status}
                              statusDetail={activity.statusDetail}
                              onSelect={() => {
                                setSelectedLatestActivityId(activity.id);
                                setDetailSheet("latest-activity");
                              }}
                            />
                          </li>
                        ))}
                    </ul>
                  }
                  detail={
                    selectedLatestActivity ? (
                      <ActivityDetail
                        activity={selectedLatestActivity}
                        memories={data.memories}
                        showFullRecord
                        onOpenActivity={openActivity}
                        onOpenMemory={openAdminMemory}
                      />
                    ) : (
                      <p className="compliance-d-empty">
                        No retention activity has been recorded in this session.
                      </p>
                    )
                  }
                  detailId="latest-activity-detail"
                  detailLabel="Latest activity details"
                  sheetOpen={detailSheet === "latest-activity"}
                  closeSheet={() => setDetailSheet(null)}
                />
              </section>

              <section className="compliance-d-section-band">
                <div className="compliance-d-section-title">
                  <h2>Simulated lifecycle notifications</h2>
                  <p>
                    Examples of notifications CUGA could publish to an audit,
                    governance, or workflow system. Until eventing is connected,
                    they remain in this PoC&apos;s local ledger.
                  </p>
                </div>
                <MasterDetail
                  listLabel="Simulated lifecycle notifications"
                  list={
                    <ul className="compliance-d-list">
                      {data.deliveries.map((delivery) => (
                        <li key={delivery.eventId}>
                          <RecordRow
                            recordId={delivery.eventId}
                            scope="delivery"
                            selected={delivery.eventId === selectedDelivery?.eventId}
                            title={delivery.title}
                            meta={delivery.deliveredAt}
                            status="Recorded locally"
                            statusDetail="No external destination"
                            onSelect={() => {
                              setSelectedDeliveryId(delivery.eventId);
                              setDetailSheet("delivery");
                            }}
                          />
                        </li>
                      ))}
                    </ul>
                  }
                  detail={
                    selectedDelivery ? (
                      <DeliveryDetail
                        delivery={selectedDelivery}
                        memories={data.memories}
                        onOpenActivity={openActivity}
                        onOpenMemory={openAdminMemory}
                      />
                    ) : (
                      <p className="compliance-d-empty">
                        Event delivery data will appear when the eventing integration is connected.
                      </p>
                    )
                  }
                  detailId="delivery-detail"
                  detailLabel="Delivery details"
                  sheetOpen={detailSheet === "delivery"}
                  closeSheet={() => setDetailSheet(null)}
                />
              </section>
            </div>
          )}

          {adminTab === "memory" && (
            <div role="tabpanel" aria-label="Memory">
              <Grid className="compliance-d-admin-page-head">
                <Column sm={4} md={8} lg={16}>
                  <p className="compliance-d-eyebrow">Governed inventory</p>
                  <h1>Agent memory</h1>
                  <p>
                    Inspect lifecycle information by person. Stored content
                    requires separate audited permission.
                  </p>
                </Column>
              </Grid>
              <Grid className="compliance-d-toolbar">
                <Column sm={4} md={4} lg={5}>
                  <Select
                    id="admin-memory-person"
                    labelText="Memory about"
                    value={adminPerson}
                    onChange={(event) => setAdminPerson(event.target.value)}
                  >
                    <SelectItem value="all" text="All people" />
                    {adminPeople.map((person) => (
                      <SelectItem key={person} value={person} text={person} />
                    ))}
                  </Select>
                </Column>
                <Column sm={4} md={4} lg={5}>
                  <Select
                    id="admin-memory-state"
                    labelText="Lifecycle state"
                    value={adminMemoryState}
                    onChange={(event) => setAdminMemoryState(event.target.value)}
                  >
                    <SelectItem value="all" text="All states" />
                    <SelectItem value="attention" text="Needs attention" />
                    <SelectItem value="Scheduled for deletion" text="Scheduled for deletion" />
                    <SelectItem value="Retained" text="Retained" />
                    <SelectItem value="Protected" text="Protected" />
                  </Select>
                </Column>
                <Column sm={4} md={8} lg={6} className="compliance-d-toolbar-summary">
                  {adminMemories.length} memories / {sourceConversationCount} source conversations
                </Column>
              </Grid>
              <MasterDetail
                listLabel="Admin memory list"
                list={
                  adminMemories.length ? (
                    renderMemoryRows(adminMemories, selectedAdminMemory?.id ?? "", true)
                  ) : (
                    <p className="compliance-d-empty">No memories match these filters.</p>
                  )
                }
                detail={
                  selectedAdminMemory ? (
                    <MemoryDetail memory={selectedAdminMemory} admin onOpenConversation={onOpenConversation} />
                  ) : (
                    <p className="compliance-d-empty">Select a memory to view its details.</p>
                  )
                }
                detailId="admin-memory-detail"
                detailLabel="Admin memory details"
                sheetOpen={detailSheet === "admin-memory"}
                closeSheet={() => setDetailSheet(null)}
              />
            </div>
          )}

          {adminTab === "activity" && (
            <div role="tabpanel" aria-label="Activity">
              <Grid className="compliance-d-admin-page-head">
                <Column sm={4} md={8} lg={16}>
                  <p className="compliance-d-eyebrow">Lifecycle evidence</p>
                  <h1>Activity</h1>
                  <p>Scheduled runs, user requests, warnings, and delivered outcomes.</p>
                </Column>
              </Grid>
              <Grid className="compliance-d-toolbar">
                <Column sm={4} md={4} lg={5}>
                  <Select
                    id="admin-activity-type"
                    labelText="Activity type"
                    value={activityType}
                    onChange={(event) => setActivityType(event.target.value)}
                  >
                    <SelectItem value="all" text="All activity" />
                    <SelectItem value="Retention run" text="Retention runs" />
                    <SelectItem value="User request" text="User requests" />
                    <SelectItem value="Warning" text="Warnings" />
                  </Select>
                </Column>
                <Column sm={4} md={4} lg={11} className="compliance-d-toolbar-summary">
                  {filteredActivities.length} activity records
                </Column>
              </Grid>
              <MasterDetail
                listLabel="Activity history"
                list={
                  <ul className="compliance-d-list">
                    {filteredActivities.map((activity) => (
                      <li key={activity.id}>
                        <RecordRow
                          recordId={activity.id}
                          scope="activity"
                          selected={activity.id === selectedActivity?.id}
                          title={activity.title}
                          meta={`${activity.timestamp} / ${activity.id}`}
                          status={activity.status}
                          statusDetail={activity.statusDetail}
                          onSelect={() => {
                            setSelectedActivityId(activity.id);
                            setDetailSheet("activity");
                          }}
                        />
                      </li>
                    ))}
                  </ul>
                }
                detail={
                  selectedActivity ? (
                    <ActivityDetail
                      activity={selectedActivity}
                      memories={data.memories}
                      showFullRecord={false}
                      onOpenActivity={openActivity}
                      onOpenMemory={openAdminMemory}
                    />
                  ) : (
                    <p className="compliance-d-empty">No activity records are available.</p>
                  )
                }
                detailId="activity-detail"
                detailLabel="Activity details"
                sheetOpen={detailSheet === "activity"}
                closeSheet={() => setDetailSheet(null)}
              />
            </div>
          )}
        </>
      )}
    </main>
  );

  if (embedded) {
    return content;
  }

  return (
    <ComposedModal
      open
      size="lg"
      isFullWidth
      preventCloseOnClickOutside
      onClose={onClose}
      className="compliance-d-modal"
    >
      <ModalHeader label={agentName} title="Memory and compliance" closeModal={onClose} />
      <ModalBody className="compliance-d-modal-body">
        {content}
      </ModalBody>
    </ComposedModal>
  );
}
