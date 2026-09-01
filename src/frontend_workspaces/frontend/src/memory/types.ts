export type MemoryState = "Retained" | "Needs attention" | "Protected";

export type RecentMemoryUsage = {
  threadId: string;
  conversationLabel: string;
  usedAt: string;
  usedLabel: string;
};

export type MemoryRecord = {
  id: string;
  entityId: string;
  entityType: string;
  title: string;
  content?: string;
  category?: string;
  ownerLabel?: string;
  sourceConversationId?: string;
  sourceLabel: string;
  createdAt?: string;
  createdLabel: string;
  lastUsedAt?: string;
  lastUsedLabel: string;
  usageCount: number;
  recentUsage: RecentMemoryUsage[];
  state: MemoryState;
  statusDetail: string;
  retentionRule?: string;
  relatedIds: string[];
  legalHold: boolean;
};

export type ProtectionStatus = {
  id: "save-check" | "send-check";
  title: string;
  description: string;
  enabled: boolean;
  healthy: boolean;
  pluginCount: number;
};

export type MemoryPage = {
  items: MemoryRecord[];
  total: number;
  nextCursor: string | null;
};

export type RetentionRule = {
  name: string;
  entityType: string;
  action: string;
  description?: string;
  maxAgeDays?: number;
  maxUnusedDays?: number;
};

export type RetentionCapabilities = {
  available: boolean;
  schedulingSupported: boolean;
  scheduleLabel: string;
  rules: RetentionRule[];
};

export type RetentionReportItem = {
  entityId?: string;
  entityType?: string;
  title?: string;
  action?: "flag" | "delete" | "skip";
  outcome?: string;
};

export type RetentionReport = {
  runId?: string;
  startedAt?: string;
  completedAt?: string;
  summary: string;
  flagged: RetentionReportItem[];
  deleted: RetentionReportItem[];
  skipped: RetentionReportItem[];
  errors: string[];
  warnings: string[];
};

export type RetentionRun = RetentionReport & {
  runId: string;
  actorId: string;
  status: string;
  createdAt: string;
};
