import { apiFetch } from "../api";
import {
  type MemoryPage,
  type MemoryRecord,
  type MemoryState,
  type ProtectionStatus,
  type RetentionCapabilities,
  type RetentionReport,
  type RetentionReportItem,
  type RetentionRun,
} from "./types";

type EvolveEntity = {
  id: string;
  type: string;
  content?: unknown;
  created_at?: string | null;
  metadata?: Record<string, unknown>;
  related_ids?: string[];
  source_thread_id?: string | null;
  source_available?: boolean;
  usage?: {
    count: number;
    last_used_at?: string | null;
    recent?: Array<{
      thread_id?: string | null;
      conversation_label?: string | null;
      used_at?: string | null;
    }>;
  };
};

type EntityInventory = {
  items: EvolveEntity[];
  total: number;
  next_cursor?: string | null;
};

type ComplianceStatusResponse = {
  plugins?: Array<{
    hooks?: string[];
    enabled?: boolean;
    healthy?: boolean;
  }>;
};

type RetentionCapabilitiesResponse = {
  retention_available: boolean;
  scheduling_supported: boolean;
  schedule?: { state: string; label: string };
  rules?: Array<{
    name: string;
    entity_type: string;
    action: string;
    description?: string;
    max_age_days?: number;
    max_unused_days?: number;
  }>;
};

type RetentionReportItemResponse = {
  entity_id?: string;
  entity_type?: string;
  title?: string;
  action?: "flag" | "delete" | "skip";
  outcome?: string;
  reason?: string;
};

type RetentionReportResponse = {
  run_id?: string;
  started_at?: string;
  completed_at?: string;
  summary?: string;
  flagged?: RetentionReportItemResponse[];
  deleted?: RetentionReportItemResponse[];
  skipped?: RetentionReportItemResponse[];
  errors?: string[];
  warnings?: string[];
};

type RetentionRunResponse = {
  run_id: string;
  actor_id: string;
  status: string;
  created_at: string;
  report: RetentionReportResponse;
};

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body.detail === "string" ? body.detail : fallback;
    throw new Error(detail);
  }
  return body as T;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  return readJson<T>(response, "Memory request failed");
}

function scopedPath(path: string, agentId: string): string {
  const separator = path.includes("?") ? "&" : "?";
  return `${path}${separator}agent_id=${encodeURIComponent(agentId)}`;
}

function contentText(content: unknown): string | undefined {
  if (typeof content === "string") return content;
  if (content == null) return undefined;
  try {
    return JSON.stringify(content, null, 2);
  } catch {
    return String(content);
  }
}

function relativeDate(value: unknown, emptyLabel: string): string {
  if (typeof value !== "string") return emptyLabel;
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return emptyLabel;
  const days = Math.max(0, Math.floor((Date.now() - timestamp) / 86_400_000));
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  return `${days} days ago`;
}

function stateFor(metadata: Record<string, unknown>): MemoryState {
  if (metadata.legal_hold === true) return "Protected";
  if (metadata.retention_flagged_at) return "Needs attention";
  return "Retained";
}

function shortReference(value: string): string {
  return value.length > 16 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

function displayType(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function titleFor(entity: EvolveEntity, content?: string): string {
  const metadata = entity.metadata ?? {};
  const explicit = metadata.title ?? metadata.display_name;
  if (typeof explicit === "string" && explicit.trim()) return explicit.trim();
  if (content) {
    const firstLine = content.split("\n", 1)[0].trim();
    if (firstLine) return firstLine.length > 96 ? `${firstLine.slice(0, 93)}...` : firstLine;
  }
  return `${displayType(entity.type)} memory`;
}

function mapEntity(entity: EvolveEntity, includeContent = true, includeOwner = false): MemoryRecord {
  const metadata = entity.metadata ?? {};
  const content = includeContent ? contentText(entity.content) : undefined;
  const threadId = String(entity.source_thread_id ?? metadata.session_id ?? metadata.thread_id ?? "");
  const usage = entity.usage ?? { count: 0, recent: [] };
  const lastUsedAt = typeof usage.last_used_at === "string" ? usage.last_used_at : undefined;
  const createdAt = typeof entity.created_at === "string" ? entity.created_at : undefined;
  const state = stateFor(metadata);
  const category = typeof metadata.category === "string" && metadata.category.trim()
    ? metadata.category.trim()
    : undefined;

  return {
    id: `memory-${entity.id}`,
    entityId: entity.id,
    entityType: entity.type,
    title: titleFor(entity, content),
    content,
    category,
    ownerLabel: includeOwner
      ? String(
          metadata.user_name ??
          metadata.display_name ??
          metadata.person ??
          metadata.user_id ??
          metadata.owner_id ??
          "Owner unavailable",
        )
      : undefined,
    sourceConversationId: entity.source_available === false ? undefined : threadId || undefined,
    sourceLabel: threadId ? `Conversation ${shortReference(threadId)}` : "No available source conversation",
    createdAt,
    createdLabel: relativeDate(createdAt, "Saved date unavailable"),
    lastUsedAt,
    lastUsedLabel: relativeDate(lastUsedAt, "Not used in an available conversation"),
    usageCount: Number.isFinite(usage.count) ? usage.count : 0,
    recentUsage: (usage.recent ?? []).map((entry) => ({
      threadId: String(entry.thread_id ?? ""),
      conversationLabel: String(entry.conversation_label ?? "Conversation"),
      usedAt: String(entry.used_at ?? ""),
      usedLabel: relativeDate(entry.used_at, "Date unavailable"),
    })),
    state,
    statusDetail:
      state === "Protected"
        ? "Legal hold"
        : state === "Needs attention"
          ? "Flagged by the published retention policy"
          : "Available",
    retentionRule: typeof metadata.retention_rule === "string" ? metadata.retention_rule : undefined,
    relatedIds: (entity.related_ids ?? []).map((id) => `memory-${id}`),
    legalHold: metadata.legal_hold === true,
  };
}

function mapReportItem(item: RetentionReportItemResponse): RetentionReportItem {
  return {
    entityId: item.entity_id,
    entityType: item.entity_type,
    title: item.title,
    action: item.action,
    outcome: item.outcome,
    reason: item.reason,
  };
}

function mapReport(report: RetentionReportResponse): RetentionReport {
  return {
    runId: report.run_id,
    startedAt: report.started_at,
    completedAt: report.completed_at,
    summary: report.summary ?? "Retention completed.",
    flagged: (report.flagged ?? []).map(mapReportItem),
    deleted: (report.deleted ?? []).map(mapReportItem),
    skipped: (report.skipped ?? []).map(mapReportItem),
    errors: report.errors ?? [],
    warnings: report.warnings ?? [],
  };
}

export async function loadMemoryPage(agentId: string, cursor?: string): Promise<MemoryPage> {
  const params = new URLSearchParams({
    agent_id: agentId,
    include_content: "true",
    limit: "200",
  });
  if (cursor) params.set("cursor", cursor);
  const inventory = await requestJson<EntityInventory>(`/api/memory/entities?${params}`);
  return {
    items: (inventory.items ?? []).map((entity) => mapEntity(entity)),
    total: inventory.total,
    nextCursor: inventory.next_cursor ?? null,
  };
}

export async function loadAdminMemoryPage(agentId: string, cursor?: string): Promise<MemoryPage> {
  const params = new URLSearchParams({ agent_id: agentId, limit: "200" });
  if (cursor) params.set("cursor", cursor);
  const inventory = await requestJson<EntityInventory>(`/api/manage/memory/entities?${params}`);
  return {
    items: (inventory.items ?? []).map((entity) => mapEntity(entity, false, true)),
    total: inventory.total,
    nextCursor: inventory.next_cursor ?? null,
  };
}

export async function loadProtectionStatus(): Promise<ProtectionStatus[]> {
  const response = await requestJson<ComplianceStatusResponse>("/api/manage/memory/compliance/status");
  const definitions: Array<Pick<ProtectionStatus, "id" | "title" | "description"> & { hook: string }> = [
    {
      id: "save-check",
      title: "Sensitive information before saving",
      description: "Checks every memory before it is stored and stops saves rejected by configured protection plugins.",
      hook: "memory_pre_write",
    },
    {
      id: "send-check",
      title: "Sensitive information before sending",
      description: "Checks messages and tool inputs before they are sent to the AI model.",
      hook: "llm_pre_call",
    },
  ];
  return definitions.map((definition) => {
    const plugins = (response.plugins ?? []).filter((plugin) => plugin.hooks?.includes(definition.hook));
    return {
      id: definition.id,
      title: definition.title,
      description: definition.description,
      enabled: plugins.some((plugin) => plugin.enabled === true),
      healthy: plugins.length > 0 && plugins.every((plugin) => plugin.healthy === true),
      pluginCount: plugins.length,
    };
  });
}

export async function loadRetentionCapabilities(): Promise<RetentionCapabilities> {
  const response = await requestJson<RetentionCapabilitiesResponse>("/api/memory/retention");
  return {
    available: response.retention_available,
    schedulingSupported: response.scheduling_supported,
    scheduleLabel: response.schedule?.label ?? "Scheduling unavailable",
    rules: (response.rules ?? []).map((rule) => ({
      name: rule.name,
      entityType: rule.entity_type,
      action: rule.action,
      description: rule.description,
      maxAgeDays: rule.max_age_days,
      maxUnusedDays: rule.max_unused_days,
    })),
  };
}

export async function loadRetentionRuns(agentId: string): Promise<RetentionRun[]> {
  const response = await requestJson<{ items: RetentionRunResponse[] }>(
    scopedPath("/api/manage/memory/retention/runs?limit=100", agentId),
  );
  return (response.items ?? []).map((run) => ({
    ...mapReport(run.report),
    runId: run.run_id,
    actorId: run.actor_id,
    status: run.status,
    createdAt: run.created_at,
  }));
}

export async function runRetention(agentId: string): Promise<RetentionReport> {
  const response = await requestJson<RetentionReportResponse>(
    scopedPath("/api/manage/memory/retention/runs", agentId),
    {
      method: "POST",
      body: JSON.stringify({}),
    },
  );
  return mapReport(response);
}

export async function deleteMemory(entityId: string, agentId: string): Promise<void> {
  await requestJson(scopedPath(`/api/memory/entities/${encodeURIComponent(entityId)}`, agentId), {
    method: "DELETE",
  });
}
