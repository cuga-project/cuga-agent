import {
  type ActivityRecord,
  type AutomationRecord,
  type ComplianceDemoData,
  type MemoryCategory,
  type MemoryRecord,
  type MemoryState,
} from "./CompliancePrototypeData";

type EvolveEntity = {
  id: string;
  type: string;
  content?: string;
  created_at: string;
  metadata: Record<string, unknown>;
  related_ids?: string[];
  usage?: {
    count: number;
    last_used_at?: string | null;
    recent?: Array<{
      thread_id?: string;
      conversation_label?: string;
      used_at?: string;
    }>;
  };
};

type EntityInventory = {
  items: EvolveEntity[];
  total: number;
  next_cursor?: string | null;
};

type CompliancePlugin = {
  name: string;
  protection_class: string;
  hooks: string[];
  enabled: boolean;
  healthy: boolean;
};

type ComplianceStatus = {
  healthy: boolean;
  evolve_version: string;
  backend: string;
  retention_available: boolean;
  plugins: CompliancePlugin[];
};

type AutomationConfig = {
  retention_enabled: number;
  retention_frequency: AutomationRecord["frequency"];
  retention_time: string;
  events_enabled: number;
  event_destination: string;
  event_type: string;
  scheduler_provider: string | null;
  scheduler_connected: boolean;
  scheduler_confirmed_enabled: boolean | null;
  scheduler_health: "healthy" | "unhealthy" | "unavailable";
  scheduler_detail: string;
  scheduler_callback_ready: boolean;
  retention_service_connected: boolean;
  retention_service_healthy: boolean;
  last_occurrence_at: string | null;
  last_occurrence_status: string | null;
  last_occurrence_trigger: "scheduler" | "simulation" | "run_now" | null;
  next_occurrence_at: string | null;
};

export type RetentionTransparency = {
  schedule: {
    state: "scheduled" | "disabled" | "not_configured" | "unreachable" | "needs_attention";
    label: string;
    detail: string;
  };
  rules: Array<{
    summary: string;
    scheduled: boolean;
    state: "scheduled" | "disabled" | "not_configured" | "unreachable" | "needs_attention";
  }>;
};

type LedgerRun = {
  record_type: "retention_run";
  run_id: string;
  agent_id: string;
  status: string;
  simulated: number;
  created_at: string;
  report: RetentionReport;
  affected_entity_ids: string[];
};

type LedgerUserRequest = {
  record_type: "user_request";
  request_id: string;
  agent_id: string;
  user_id: string;
  entity_id: string;
  action: "forget";
  status: "completed";
  created_at: string;
};

type LedgerDelivery = {
  delivery_id: string;
  event_id: string;
  run_id: string;
  agent_id: string;
  status: string;
  simulated: number;
  delivered_at: string;
  payload: { entity_id?: string; conversation_id?: string; run_id?: string; event_id?: string; destination?: string; event_type?: string };
};

export type RetentionReportItem = {
  entity_id: string;
  action: "flag" | "delete" | "skip";
  outcome: string;
};

export type RetentionReport = {
  run_id: string;
  completed_at: string;
  dry_run: boolean;
  summary: string;
  flagged: RetentionReportItem[];
  deleted: RetentionReportItem[];
  skipped: RetentionReportItem[];
  errors: string[];
  warnings: string[];
};

export const DEFAULT_RETENTION_POLICY = {
  rules: [
    {
      name: "unused-guidelines",
      entity_type: "guideline",
      max_unused_days: 180,
      action: "delete",
      on_missing_access_signal: "skip",
    },
    {
      name: "stale-guidelines",
      entity_type: "guideline",
      max_age_days: 90,
      action: "flag",
    },
    {
      name: "old-sessions",
      entity_type: "trajectory",
      max_age_days: 365,
      action: "delete",
      cascade_derived: true,
    },
  ],
};

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: "same-origin",
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

async function getAllEntities(url: string): Promise<EntityInventory> {
  const items: EvolveEntity[] = [];
  let cursor: string | null = null;
  let total = 0;
  do {
    const separator = url.includes("?") ? "&" : "?";
    const page = await getJson<EntityInventory>(
      `${url}${separator}limit=200${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
    );
    items.push(...page.items);
    total = page.total;
    cursor = page.next_cursor ?? null;
  } while (cursor);
  return { items, total, next_cursor: null };
}

function categoryFor(entity: EvolveEntity): MemoryCategory {
  const category = String(entity.metadata.category ?? "").toLowerCase();
  if (entity.type === "trajectory") return "Conversation";
  if (entity.type === "guideline" || entity.type === "policy") return "Guidance";
  if (category.includes("preference") || category.includes("style")) return "Preference";
  if (category.includes("work") || category.includes("role")) return "Work context";
  return "Customer fact";
}

function relativeDate(value: unknown): string {
  if (typeof value !== "string") return "Usage history unavailable";
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return "Usage history unavailable";
  const days = Math.max(0, Math.round((Date.now() - timestamp) / 86_400_000));
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return `${days} days ago`;
}

function stateFor(metadata: Record<string, unknown>): MemoryState {
  if (metadata.legal_hold) return "Protected";
  if (metadata.retention_flagged_at) return "Needs attention";
  return "Retained";
}

function entityTypeFor(type: string): MemoryRecord["entityType"] {
  if (type === "guideline" || type === "trajectory" || type === "policy") return type;
  return "fact";
}

function shortReference(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function mapEntities(items: EvolveEntity[], revealContent: boolean): MemoryRecord[] {
  const memories = items.map((entity): MemoryRecord => {
    const metadata = entity.metadata ?? {};
    const person = String(
      metadata.user_name ??
        metadata.display_name ??
        metadata.person ??
        metadata.user_id ??
        metadata.owner_id ??
        "Current user",
    );
    const sessionId = String(metadata.session_id ?? metadata.thread_id ?? "");
    const createdAt = Date.parse(entity.created_at);
    const createdDaysAgo = Number.isNaN(createdAt)
      ? 0
      : Math.max(0, Math.round((Date.now() - createdAt) / 86_400_000));
    const usage = entity.usage ?? { count: 0, recent: [] };
    const lastAccessed = entity.usage ? usage.last_used_at : metadata.last_accessed;
    const state = stateFor(metadata);

    return {
      id: `memory-${entity.id}`,
      entityId: entity.id,
      entityType: entityTypeFor(entity.type),
      title: String(
        revealContent
          ? entity.content ?? metadata.title ?? `${entity.type} memory`
          : metadata.title ?? `${entity.type} memory`,
      ),
      rememberedContent: revealContent ? entity.content : undefined,
      person,
      category: categoryFor(entity),
      sourceConversationId: sessionId || undefined,
      sourceLabel: sessionId ? `Conversation ${shortReference(sessionId)}` : "Stored directly",
      createdDaysAgo,
      lastUsedLabel: relativeDate(lastAccessed),
      usageHistoryAvailable: usage.count > 0,
      usageCount: usage.count,
      recentUsage: (usage.recent ?? []).map((entry) => ({
        threadId: String(entry.thread_id ?? ""),
        conversationLabel: String(entry.conversation_label ?? "Conversation"),
        usedAt: String(entry.used_at ?? ""),
        usedLabel: relativeDate(entry.used_at),
      })),
      state,
      statusDetail:
        state === "Protected"
          ? "Legal hold"
          : state === "Needs attention"
            ? String(metadata.retention_rule ?? "Retention review")
            : "Available to the agent",
      why: "The agent may use this memory when it is relevant to your work.",
      lifecycle:
        state === "Protected"
          ? "Protected from deletion by a legal hold."
          : state === "Needs attention"
            ? "Flagged by the latest retention evaluation."
            : "Retained under the current policy.",
      relatedIds: [],
      legalHold: Boolean(metadata.legal_hold),
    };
  });

  return memories.map((memory, index) => ({
    ...memory,
    relatedIds: (items[index].related_ids ?? []).map((id) => `memory-${id}`),
  }));
}

function applyStatus(
  automations: AutomationRecord[],
  status: ComplianceStatus | null,
): AutomationRecord[] {
  if (!status) {
    return automations.map((automation) => ({
      ...automation,
      runtime: "unavailable",
      health: "Status unavailable",
      enabled: false,
      latest: "Live status unavailable",
    }));
  }
  const writePlugins = status.plugins.filter((plugin) =>
    plugin.hooks.includes("memory_pre_write"),
  );
  const sendPlugins = status.plugins.filter((plugin) =>
    plugin.hooks.includes("llm_pre_call"),
  );

  return automations.map((automation) => {
    if (automation.id === "save-check") {
      const healthy = writePlugins.length > 0 && writePlugins.every((plugin) => plugin.healthy);
      return {
        ...automation,
        runtime: "active",
        enabled: writePlugins.some((plugin) => plugin.enabled),
        health: healthy ? "Healthy" : "Not running",
        latest: `${writePlugins.length} configured write protection ${writePlugins.length === 1 ? "plugin" : "plugins"}`,
        proposed: false,
      };
    }
    if (automation.id === "send-check") {
      const healthy = sendPlugins.length > 0 && sendPlugins.every((plugin) => plugin.healthy);
      return {
        ...automation,
        runtime: "active",
        enabled: sendPlugins.some((plugin) => plugin.enabled),
        health: healthy ? "Healthy" : "Not running",
        latest: `${sendPlugins.length} configured model-egress ${sendPlugins.length === 1 ? "plugin" : "plugins"}`,
        proposed: false,
      };
    }
    if (automation.id === "retention") {
      return {
        ...automation,
        runtime: "configured",
        health: "Configured only",
        latest: status.retention_available
          ? `Retention is available in Evolve ${status.evolve_version}`
          : "Retention is unavailable",
        proposed: false,
      };
    }
    return automation;
  });
}

export async function loadLiveComplianceData(
  fallback: ComplianceDemoData,
  canManage: boolean,
): Promise<{
  data: ComplianceDemoData;
  userMemories: MemoryRecord[];
  retention: RetentionTransparency;
}> {
  const [userInventory, retention] = await Promise.all([
    getAllEntities("/api/memory/entities?include_content=true"),
    getJson<RetentionTransparency>("/api/memory/retention"),
  ]);
  let adminInventory: EntityInventory | null = null;
  let complianceStatus: ComplianceStatus | null = null;

  let activity: ActivityRecord[] = [];
  let deliveries: DeliveryRecord[] = [];
  let automationConfig: AutomationConfig | null = null;
  if (canManage) {
    const [runs, delivered] = await Promise.all([
      getJson<{ items: Array<LedgerRun | LedgerUserRequest> }>("/api/admin/memory/activity?limit=100"),
      getJson<{ items: LedgerDelivery[] }>("/api/admin/memory/deliveries?limit=100"),
    ]);
    const outcomeByDeliveryKey = new Map<string, string>();
    runs.items.forEach((run) => {
      if (run.record_type !== "retention_run") return;
      const outcomes = [
        ...(run.report?.flagged ?? []),
        ...(run.report?.deleted ?? []),
        ...(run.report?.skipped ?? []),
      ];
      outcomes.forEach((outcome) => {
        const label =
          outcome.action === "delete"
            ? "Deletion match"
            : outcome.action === "flag"
              ? "Review requested"
              : "Kept because evidence was incomplete";
        outcomeByDeliveryKey.set(`${run.run_id}:${outcome.entity_id}`, label);
      });
    });
    activity = runs.items.map((run) => {
      if (run.record_type === "user_request") {
        return {
          id: run.request_id,
          type: "User request",
          title: "Memory forgotten",
          timestamp: new Date(run.created_at).toLocaleString(),
          status: "Automatic",
          statusDetail: "Completed",
          summary: "A user deleted one of their memories.",
          facts: [
            { label: "Action", value: "Forget" },
            { label: "Status", value: "Completed" },
          ],
          affectedMemoryIds: [`memory-${run.entity_id}`],
        };
      }
      const report = run.report ?? ({} as RetentionReport);
      const simulated = Boolean(run.simulated);
      const flaggedCount = report.flagged?.length ?? 0;
      const deletedCount = report.deleted?.length ?? 0;
      const skippedCount = report.skipped?.length ?? 0;
      const hasErrors = Boolean(report.errors?.length);
      return {
        id: run.run_id,
        type: "Retention run",
        title: `${simulated ? "Retention simulation" : "Scheduled retention"} · ${new Date(run.created_at).toLocaleDateString()}`,
        timestamp: new Date(run.created_at).toLocaleString(),
        status: hasErrors ? "Incomplete" : simulated ? "Simulation" : "Automatic",
        statusDetail: hasErrors
          ? "Completed with errors"
          : simulated
            ? "Preview completed"
            : "Completed automatically",
        summary: `Retention found ${flaggedCount} for review, ${deletedCount} deletion matches, and ${skippedCount} kept because evidence was incomplete.`,
        facts: [
          { label: "Run ID", value: run.run_id },
          {
            label: "Trigger",
            value: simulated ? "Simulation of the configured schedule" : "Connected scheduler",
          },
          {
            label: "Policy matches",
            value: `${run.affected_entity_ids.length} (${flaggedCount} review, ${deletedCount} deletion)`,
          },
        ],
        affectedMemoryIds: run.affected_entity_ids.map((id) => `memory-${id}`),
      };
    });
    deliveries = delivered.items.map((item) => {
      const simulated = Boolean(item.simulated);
      return {
        eventId: item.event_id,
        eventType: item.payload.event_type ?? "retention.outcome",
        title: simulated ? "Simulated retention outcome" : "Scheduled retention outcome",
        deliveredAt: new Date(item.delivered_at).toLocaleString(),
        deliveryId: item.delivery_id,
        attempt: "1",
        destination: item.payload.destination ?? "Local proof-of-concept ledger",
        correlationId: item.run_id,
        relatedActivityId: item.run_id,
        affectedMemoryId: item.payload.entity_id ? `memory-${item.payload.entity_id}` : undefined,
        outcomeLabel: outcomeByDeliveryKey.get(
          `${item.run_id}:${item.payload.entity_id ?? ""}`,
        ),
        fields: [
          { name: "Run ID", value: item.run_id },
          { name: "Entity ID", value: item.payload.entity_id ?? "Unavailable" },
          { name: "Conversation ID", value: item.payload.conversation_id ?? "Unavailable" },
        ],
        privacyNote: `${simulated ? "Simulated and " : ""}recorded locally. No external event transport is connected.`,
      };
    });
    [adminInventory, complianceStatus] = await Promise.all([
      getAllEntities("/api/admin/memory/entities"),
      getJson<ComplianceStatus>("/api/admin/memory/compliance/status"),
    ]);
    automationConfig = await getJson<AutomationConfig>("/api/admin/memory/automation");
  }

  const userMemories = mapEntities(userInventory.items, true);
  const allMemories = mapEntities(adminInventory?.items ?? userInventory.items, false);
  return {
    data: {
      memories: allMemories,
      automations: applyStatus(fallback.automations, complianceStatus).map((automation) => {
        if (!automationConfig) return automation;
        if (automation.id === "retention") {
          const confirmed = automationConfig.scheduler_confirmed_enabled;
          const connected = Boolean(automationConfig.scheduler_connected);
          const healthy = automationConfig.scheduler_health === "healthy";
          return {
            ...automation,
            runtime: confirmed && healthy ? ("active" as const) : connected ? ("configured" as const) : ("unavailable" as const),
            health: confirmed && healthy ? ("Healthy" as const) : connected ? ("Not running" as const) : ("Status unavailable" as const),
            enabled: Boolean(automationConfig.retention_enabled),
            frequency: automationConfig.retention_frequency,
            time: automationConfig.retention_time,
            schedule: `${automationConfig.retention_frequency} at ${automationConfig.retention_time}`,
            schedulerProvider: automationConfig.scheduler_provider,
            schedulerConnected: connected,
            schedulerConfirmedEnabled: confirmed,
            schedulerHealth: automationConfig.scheduler_health,
            schedulerDetail: automationConfig.scheduler_detail,
            lastOccurrenceAt: automationConfig.last_occurrence_at,
            lastOccurrenceStatus: automationConfig.last_occurrence_status,
            lastOccurrenceTrigger: automationConfig.last_occurrence_trigger,
            nextOccurrenceAt: automationConfig.next_occurrence_at,
            latest: automationConfig.last_occurrence_at
              ? `Last occurrence ${automationConfig.last_occurrence_status ?? "recorded"}`
              : "No scheduled occurrence recorded",
          };
        }
        if (automation.id === "events") return { ...automation, runtime: "configured" as const, health: "Configured only" as const, enabled: Boolean(automationConfig.events_enabled), destination: automationConfig.event_destination, latest: `${automationConfig.event_type} · local ledger only` };
        return automation;
      }),
      activities: activity,
      deliveries: deliveries.map((delivery) => {
        const memory = delivery.affectedMemoryId
          ? allMemories.find((candidate) => candidate.id === delivery.affectedMemoryId)
          : undefined;
        const relatedActivity = activity.find(
          (candidate) => candidate.id === delivery.relatedActivityId,
        );
        return {
          ...delivery,
          relatedActivityTitle: relatedActivity?.title ?? "Retention simulation",
          title: memory
            ? `${memory.title} · ${delivery.outcomeLabel ?? "Retention outcome"}`
            : delivery.title,
        };
      }),
    },
    userMemories,
    retention,
  };
}

export async function runRetentionPreview(): Promise<RetentionReport> {
  return getJson<RetentionReport>("/api/admin/memory/scheduled-runs", {
    method: "POST",
    body: JSON.stringify({
      policy: DEFAULT_RETENTION_POLICY,
      dry_run: true,
    }),
  });
}

export async function bootstrapComplianceDemo(): Promise<{ memory_count: number; created_entities: number; protection_status: ComplianceStatus }> {
  return getJson("/api/admin/memory/poc/bootstrap", { method: "POST" });
}

export async function deleteLiveMemory(entityId: string): Promise<void> {
  await getJson(`/api/memory/entities/${encodeURIComponent(entityId)}`, { method: "DELETE" });
}

export async function saveAutomationConfig(record: AutomationRecord): Promise<void> {
  await getJson("/api/admin/memory/automation", {
    method: "PATCH",
    body: JSON.stringify({
      retention_enabled: record.id === "retention" ? record.enabled : undefined,
      retention_frequency: record.id === "retention" ? record.frequency : undefined,
      retention_time: record.id === "retention" ? record.time : undefined,
      event_destination: record.id === "events" ? record.destination : undefined,
      events_enabled: record.id === "events" ? record.enabled : undefined,
    }),
  });
}
