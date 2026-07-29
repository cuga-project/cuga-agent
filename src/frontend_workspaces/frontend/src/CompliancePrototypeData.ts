export type MemoryCategory =
  | "Work context"
  | "Preference"
  | "Customer fact"
  | "Guidance"
  | "Conversation";

export type MemoryState =
  | "Retained"
  | "Needs attention"
  | "Scheduled for deletion"
  | "Deleted"
  | "Protected";

export type MemoryRecord = {
  id: string;
  entityId: string;
  entityType: "guideline" | "trajectory" | "fact" | "policy";
  title: string;
  rememberedContent?: string;
  person: string;
  category: MemoryCategory;
  sourceConversationId?: string;
  sourceLabel: string;
  createdDaysAgo: number;
  lastUsedLabel: string;
  usageHistoryAvailable: boolean;
  usageCount?: number;
  recentUsage?: Array<{
    threadId: string;
    conversationLabel: string;
    usedAt: string;
    usedLabel: string;
  }>;
  state: MemoryState;
  statusDetail: string;
  why: string;
  lifecycle: string;
  relatedIds: string[];
  legalHold?: boolean;
  canonicalOutcome?: {
    preview: "delete" | "flag" | "skip" | "keep";
    applied: "delete" | "flag" | "skip" | "keep" | "protected";
    reason: string;
    rule: string;
    detail: string;
  };
};

export type AutomationRecord = {
  id: "save-check" | "send-check" | "retention" | "events";
  title: string;
  description: string;
  enabled: boolean;
  schedule: string;
  latest: string;
  kind: "protection" | "retention" | "events";
  frequency?: "Every day" | "Every week" | "Every month";
  time?: string;
  destination?: string;
  proposed?: boolean;
  runtime: "active" | "configured" | "unavailable";
  health: "Healthy" | "Not running" | "Configured only" | "Status unavailable";
  schedulerProvider?: string | null;
  schedulerConnected?: boolean;
  schedulerConfirmedEnabled?: boolean | null;
  schedulerHealth?: "healthy" | "unhealthy" | "unavailable";
  schedulerDetail?: string;
  lastOccurrenceAt?: string | null;
  lastOccurrenceStatus?: string | null;
  lastOccurrenceTrigger?: "scheduler" | "simulation" | "run_now" | null;
  nextOccurrenceAt?: string | null;
};

export type ActivityRecord = {
  id: string;
  type: "Retention run" | "Warning" | "User request";
  title: string;
  timestamp: string;
  status: "Attention" | "Warning" | "Processing" | "Automatic" | "Simulation" | "Incomplete";
  statusDetail: string;
  summary: string;
  facts: Array<{ label: string; value: string }>;
  affectedMemoryIds: string[];
  notice?: { title: string; text: string };
};

export type DeliveryRecord = {
  eventId: string;
  eventType: string;
  title: string;
  deliveredAt: string;
  deliveryId: string;
  attempt: string;
  destination: string;
  correlationId: string;
  relatedActivityId: string;
  relatedActivityTitle?: string;
  affectedMemoryId?: string;
  outcomeLabel?: string;
  fields: Array<{ name: string; value: string }>;
  privacyNote: string;
};

export type ComplianceDemoData = {
  memories: MemoryRecord[];
  automations: AutomationRecord[];
  activities: ActivityRecord[];
  deliveries: DeliveryRecord[];
};

const coreMemories: MemoryRecord[] = [
  {
    id: "memory-4",
    entityId: "4",
    entityType: "trajectory",
    title: "Conversation 4",
    person: "Dana Whitfield",
    category: "Conversation",
    sourceConversationId: "T1",
    sourceLabel: "Conversation 4",
    createdDaysAgo: 400,
    lastUsedLabel: "400 days ago",
    usageHistoryAvailable: true,
    state: "Scheduled for deletion",
    statusDetail: "Older than 365 days",
    why: "This old conversation and the memories created from it are included in the next retention run.",
    lifecycle: "Scheduled for deletion with three related memories.",
    relatedIds: ["memory-3", "memory-7", "memory-11"],
    canonicalOutcome: {
      preview: "delete",
      applied: "delete",
      reason: "age",
      rule: "old-sessions",
      detail: "created 400.0d ago > max_age_days=365",
    },
  },
  {
    id: "memory-3",
    entityId: "3",
    entityType: "guideline",
    title: "Work context 3",
    person: "Dana Whitfield",
    category: "Work context",
    sourceConversationId: "T1",
    sourceLabel: "Conversation 4",
    createdDaysAgo: 220,
    lastUsedLabel: "Usage history unavailable",
    usageHistoryAvailable: false,
    state: "Scheduled for deletion",
    statusDetail: "Linked to old Conversation 4",
    why: "ACME Support uses this context when tailoring support guidance to Dana's role.",
    lifecycle: "Scheduled for deletion with the old conversation that created it.",
    relatedIds: ["memory-4", "memory-7", "memory-11"],
    canonicalOutcome: {
      preview: "delete",
      applied: "delete",
      reason: "cascade:T1",
      rule: "old-sessions",
      detail:
        "derived from session 4 (metadata.source_task_id == T1), which this rule deletes",
    },
  },
  {
    id: "memory-7",
    entityId: "7",
    entityType: "fact",
    title: "Customer fact 7",
    person: "Dana Whitfield",
    category: "Customer fact",
    sourceConversationId: "T1",
    sourceLabel: "Conversation 4",
    createdDaysAgo: 210,
    lastUsedLabel: "Usage history unavailable",
    usageHistoryAvailable: false,
    state: "Scheduled for deletion",
    statusDetail: "Correction request MR-392 is processing",
    why: "ACME Support may use this customer fact when preparing billing support.",
    lifecycle: "Scheduled for deletion with its source conversation. A correction request is also processing.",
    relatedIds: ["memory-4", "memory-3", "memory-11"],
    canonicalOutcome: {
      preview: "delete",
      applied: "delete",
      reason: "cascade:T1",
      rule: "old-sessions",
      detail:
        "derived from session 4 (metadata.source_task_id == T1), which this rule deletes",
    },
  },
  {
    id: "memory-11",
    entityId: "11",
    entityType: "guideline",
    title: "Preference 11",
    person: "Dana Whitfield",
    category: "Preference",
    sourceConversationId: "T1",
    sourceLabel: "Conversation 4",
    createdDaysAgo: 180,
    lastUsedLabel: "Usage history unavailable",
    usageHistoryAvailable: false,
    state: "Scheduled for deletion",
    statusDetail: "Linked to old Conversation 4",
    why: "ACME Support may use this preference when formatting responses.",
    lifecycle: "Scheduled for deletion with the old conversation that created it.",
    relatedIds: ["memory-4", "memory-3", "memory-7"],
    canonicalOutcome: {
      preview: "delete",
      applied: "delete",
      reason: "cascade:T1",
      rule: "old-sessions",
      detail:
        "derived from session 4 (metadata.source_task_id == T1), which this rule deletes",
    },
  },
  {
    id: "memory-9",
    entityId: "9",
    entityType: "trajectory",
    title: "Conversation 9",
    person: "Carlos Mendes",
    category: "Conversation",
    sourceConversationId: "T2",
    sourceLabel: "Conversation 9",
    createdDaysAgo: 500,
    lastUsedLabel: "480 days ago",
    usageHistoryAvailable: true,
    state: "Scheduled for deletion",
    statusDetail: "Older than 365 days",
    why: "This old conversation and the memory created from it are included in the next retention run.",
    lifecycle: "Scheduled for deletion with one related memory.",
    relatedIds: ["memory-12"],
    canonicalOutcome: {
      preview: "delete",
      applied: "delete",
      reason: "age",
      rule: "old-sessions",
      detail: "created 500.0d ago > max_age_days=365",
    },
  },
  {
    id: "memory-12",
    entityId: "12",
    entityType: "guideline",
    title: "Guidance 12",
    person: "Dana Whitfield",
    category: "Guidance",
    sourceConversationId: "T2",
    sourceLabel: "Conversation 9",
    createdDaysAgo: 300,
    lastUsedLabel: "300 days ago",
    usageHistoryAvailable: true,
    state: "Protected",
    statusDetail: "Legal hold",
    why: "ACME Support uses this guidance for a governed support workflow.",
    lifecycle:
      "The latest run attempted deletion, but a legal hold kept this memory while all other items continued.",
    relatedIds: ["memory-9"],
    legalHold: true,
    canonicalOutcome: {
      preview: "delete",
      applied: "protected",
      reason: "cascade:T2",
      rule: "old-sessions",
      detail:
        "derived from session 9 (metadata.source_task_id == T2), which this rule deletes",
    },
  },
  {
    id: "memory-5",
    entityId: "5",
    entityType: "guideline",
    title: "Billing escalation guidance",
    person: "Dana Whitfield",
    category: "Guidance",
    sourceLabel: "April 16 conversation",
    createdDaysAgo: 240,
    lastUsedLabel: "190 days ago",
    usageHistoryAvailable: true,
    state: "Scheduled for deletion",
    statusDetail: "Unused for more than 180 days",
    why: "ACME Support uses this guidance to route billing disputes.",
    lifecycle: "Scheduled for deletion because it has not been used within the published retention period.",
    relatedIds: [],
    canonicalOutcome: {
      preview: "delete",
      applied: "delete",
      reason: "unused",
      rule: "unused-guidelines",
      detail:
        "not read for 190.0d > max_unused_days=180 (from metadata.last_accessed)",
    },
  },
  {
    id: "memory-1",
    entityId: "1",
    entityType: "guideline",
    title: "Review older account guidance",
    person: "Dana Whitfield",
    category: "Guidance",
    sourceLabel: "January 6 conversation",
    createdDaysAgo: 200,
    lastUsedLabel: "30 days ago",
    usageHistoryAvailable: true,
    state: "Needs attention",
    statusDetail: "Older than 90 days",
    why: "ACME Support uses this guidance when reviewing older account issues.",
    lifecycle: "Kept and sent for review because it is older than the published review period.",
    relatedIds: [],
    canonicalOutcome: {
      preview: "flag",
      applied: "flag",
      reason: "age",
      rule: "stale-guidelines",
      detail: "created 200.0d ago > max_age_days=90",
    },
  },
  {
    id: "memory-2",
    entityId: "2",
    entityType: "guideline",
    title: "Guidance 2",
    person: "Dana Whitfield",
    category: "Guidance",
    sourceLabel: "No source conversation recorded",
    createdDaysAgo: 400,
    lastUsedLabel: "Usage history unavailable",
    usageHistoryAvailable: false,
    state: "Needs attention",
    statusDetail: "Kept because usage history is missing",
    why: "ACME Support may use this guidance when answering account questions.",
    lifecycle: "Kept rather than deleted because the system could not reliably determine when it was last used.",
    relatedIds: [],
    canonicalOutcome: {
      preview: "skip",
      applied: "skip",
      reason: "unused",
      rule: "unused-guidelines",
      detail:
        "matched but not deleted: not read for 400.0d > max_unused_days=180 — no metadata.last_accessed on this entity, so disuse was measured from created_at; enable AccessStampPlugin (or call EvolveClient.record_access) for a real recall signal; on_missing_access_signal=skip",
    },
  },
  {
    id: "memory-6",
    entityId: "6",
    entityType: "guideline",
    title: "You manage the West Coast support team",
    person: "Dana Whitfield",
    category: "Work context",
    sourceLabel: "July 12 conversation",
    createdDaysAgo: 30,
    lastUsedLabel: "2 days ago",
    usageHistoryAvailable: true,
    state: "Retained",
    statusDetail: "Current",
    why: "ACME Support uses this memory to tailor guidance to Dana's role and responsibilities.",
    lifecycle: "Retained while it remains useful and within the published retention period.",
    relatedIds: [],
    canonicalOutcome: {
      preview: "keep",
      applied: "keep",
      reason: "No rule matched",
      rule: "None",
      detail: "No retention rule matched this memory.",
    },
  },
  {
    id: "memory-8",
    entityId: "8",
    entityType: "fact",
    title: "Enterprise support queue uses priority routing",
    person: "Dana Whitfield",
    category: "Customer fact",
    sourceLabel: "July 15 conversation",
    createdDaysAgo: 10,
    lastUsedLabel: "Yesterday",
    usageHistoryAvailable: true,
    state: "Retained",
    statusDetail: "Current",
    why: "ACME Support uses this fact when prioritizing enterprise support.",
    lifecycle: "Retained while it remains useful and within the published retention period.",
    relatedIds: [],
    canonicalOutcome: {
      preview: "keep",
      applied: "keep",
      reason: "No rule matched",
      rule: "None",
      detail: "No retention rule matched this memory.",
    },
  },
  {
    id: "memory-10",
    entityId: "10",
    entityType: "policy",
    title: "Priya's refund approval policy",
    person: "Priya Raman",
    category: "Guidance",
    sourceLabel: "April 20 conversation",
    createdDaysAgo: 95,
    lastUsedLabel: "40 days ago",
    usageHistoryAvailable: true,
    state: "Retained",
    statusDetail: "Current",
    why: "ACME Support uses this policy when routing refund approvals.",
    lifecycle: "Retained because the published guidance rules do not apply to this policy.",
    relatedIds: [],
    canonicalOutcome: {
      preview: "keep",
      applied: "keep",
      reason: "No rule matched",
      rule: "None",
      detail: "No retention rule matched this memory type.",
    },
  },
];

const supplementalTitles = [
  "You lead quarterly support planning",
  "Your team supports enterprise accounts",
  "Use metric units",
  "Use a dark interface theme",
  "Summarize action items at the end",
  "Keep customer-facing replies concise",
  "Escalate payment failures after two attempts",
  "Dana Whitfield is the billing contact",
  "Use Pacific time for scheduling",
  "Archive resolved incidents after review",
  "The West Coast queue opens at 07:00",
  "Include ticket links in handoff notes",
  "Prioritize customers with active outages",
  "Use the customer's preferred language",
  "Quarterly plans include capacity forecasts",
  "Ask before changing an account owner",
  "Show dates in month-day-year format",
  "Enterprise renewals need a 30-day reminder",
  "Summaries should name the next owner",
  "The legacy billing alias may be outdated",
  "Use short headings in customer updates",
  "Escalation notes include impact and urgency",
  "Remove temporary launch guidance next cycle",
  "Keep accessibility checks in release reviews",
  "Support planning happens on the first Monday",
  "Draft replies should avoid internal acronyms",
  "Carlos owns LATAM support planning",
  "Carlos prefers Spanish summaries",
  "LATAM escalations use regional coverage",
  "Include local holidays in staffing plans",
  "Carlos reviews priority account handoffs",
  "Priya owns refund operations",
  "Priya prefers concise approval summaries",
  "Refunds over $500 need owner verification",
  "Priya reviews billing workflow changes",
  "Include evidence links in approval notes",
] as const;

const categoryCycle: MemoryCategory[] = [
  "Work context",
  "Preference",
  "Guidance",
  "Customer fact",
];

function createSupplementalMemories(): MemoryRecord[] {
  return supplementalTitles.map((title, index) => {
    const sequence = index + 13;
    const person =
      index < 26 ? "Dana Whitfield" : index < 31 ? "Carlos Mendes" : "Priya Raman";
    const category =
      (
        [
          "Work context",
          "Work context",
          "Preference",
          "Preference",
          "Preference",
          "Preference",
          "Guidance",
          "Customer fact",
        ] as MemoryCategory[]
      )[index] ?? categoryCycle[index % categoryCycle.length];
    const missingUsage = index === 7 || index === 19;
    const scheduled = index === 9 || index === 22;
    const needsAttention = missingUsage || index === 17;
    const sourceNumber = (index % 14) + 10;
    const sourceConversationId = `CONV-${String(sourceNumber).padStart(3, "0")}`;
    const state: MemoryState = scheduled
      ? "Scheduled for deletion"
      : needsAttention
        ? "Needs attention"
        : "Retained";

    return {
      id: `memory-${sequence}`,
      entityId: String(sequence),
      entityType:
        category === "Customer fact"
          ? "fact"
          : category === "Guidance"
            ? "guideline"
            : "guideline",
      title,
      person,
      category,
      sourceConversationId,
      sourceLabel: `${["July", "June", "May", "April"][index % 4]} ${3 + (index % 24)} conversation`,
      createdDaysAgo: 18 + index * 6,
      lastUsedLabel: missingUsage
        ? "Usage history unavailable"
        : index % 6 === 0
          ? "Yesterday"
          : `${2 + (index % 28)} days ago`,
      usageHistoryAvailable: !missingUsage,
      state,
      statusDetail: scheduled
        ? "Included in the next retention run"
        : missingUsage
          ? "Usage history unavailable"
          : needsAttention
            ? "Review requested"
            : "Current",
      why: `ACME Support uses this ${category.toLowerCase()} when assisting ${person.split(" ")[0]}.`,
      lifecycle: scheduled
        ? "Scheduled for deletion at the next retention run."
        : missingUsage
          ? "Kept for review because reliable usage history is unavailable."
          : "Retained while it remains useful and within the published retention period.",
      relatedIds: [],
    };
  });
}

const automations: AutomationRecord[] = [
  {
    id: "save-check",
    title: "Sensitive information before saving",
    description:
      "Checks every memory before it is stored. If a check fails, the unsafe save is stopped.",
    enabled: true,
    runtime: "active",
    health: "Healthy",
    schedule: "Continuous",
    latest: "Checked 18 memories today",
    kind: "protection",
  },
  {
    id: "send-check",
    title: "Sensitive information before sending",
    description:
      "Checks messages and tool inputs before they are sent to the AI model.",
    enabled: true,
    runtime: "active",
    health: "Healthy",
    schedule: "Continuous",
    latest: "Checked 126 model requests today",
    kind: "protection",
  },
  {
    id: "retention",
    title: "Scheduled retention",
    description:
      "Evaluates the published retention rules on the configured schedule.",
    enabled: true,
    runtime: "unavailable",
    health: "Status unavailable",
    schedule: "Every week at 02:00",
    latest: "No scheduled occurrence recorded",
    kind: "retention",
    frequency: "Every week",
    time: "02:00",
    schedulerProvider: null,
    schedulerConnected: false,
    schedulerConfirmedEnabled: null,
    schedulerHealth: "unavailable",
    schedulerDetail: "No scheduler is configured",
    lastOccurrenceAt: null,
    lastOccurrenceStatus: null,
    nextOccurrenceAt: null,
    proposed: false,
  },
  {
    id: "events",
    title: "Lifecycle event delivery",
    description:
      "Records example lifecycle payloads locally. An external event transport is not connected.",
    enabled: true,
    runtime: "configured",
    health: "Configured only",
    schedule: "After a simulated retention outcome",
    latest: "No external delivery connected",
    kind: "events",
    destination: "Memory lifecycle destination",
    proposed: true,
  },
];

const activities: ActivityRecord[] = [
  {
    id: "R-240724-0200",
    type: "Retention run",
    title: "Retention run R-240724-0200 completed",
    timestamp: "Today at 02:01",
    status: "Attention",
    statusDetail: "6 deleted, 1 protected",
    summary:
      "6 memories were deleted, 1 was sent for review, 1 was protected by legal hold, and 1 was kept because usage history is incomplete.",
    notice: {
      title: "One item remained",
      text: "Guidance 12 is protected by a legal hold. The remaining items were still processed.",
    },
    facts: [
      { label: "Run ID", value: "R-240724-0200" },
      { label: "Configuration", value: "Published version 7" },
      { label: "Schedule", value: "Every day at 02:00" },
      { label: "Scope", value: "12 memories" },
      { label: "Preview outcome", value: "1 review, 7 deletions, 1 kept on an uncertain signal" },
      { label: "Applied outcome", value: "1 review, 6 deletions, 1 protected, 1 kept on an uncertain signal" },
      { label: "Delivery", value: "9 outcome events delivered" },
    ],
    affectedMemoryIds: [
      "memory-12",
      "memory-4",
      "memory-3",
      "memory-7",
      "memory-11",
      "memory-9",
      "memory-5",
      "memory-1",
      "memory-2",
    ],
  },
  {
    id: "W-1041",
    type: "Warning",
    title: "Warning W-1041: usage history missing",
    timestamp: "Today at 02:00",
    status: "Warning",
    statusDetail: "4 memories affected",
    summary: "4 of 12 memories do not have reliable usage history.",
    notice: {
      title: "Kept on an uncertain signal",
      text: "Guidance 2 was kept rather than deleted because the system could not reliably determine when it was last used.",
    },
    facts: [
      { label: "Warning ID", value: "W-1041" },
      { label: "Related run", value: "R-240724-0200" },
      { label: "Affected", value: "4 memories" },
      { label: "Outcome", value: "1 kept and sent for attention" },
    ],
    affectedMemoryIds: ["memory-2", "memory-3", "memory-7", "memory-11"],
  },
  {
    id: "R-240724-0200-start",
    type: "Retention run",
    title: "Retention run R-240724-0200 started",
    timestamp: "Today at 01:59",
    status: "Automatic",
    statusDetail: "12 evaluated",
    summary: "ACME Support began evaluating the published retention configuration.",
    facts: [
      { label: "Run ID", value: "R-240724-0200" },
      { label: "Configuration", value: "Published version 7" },
      { label: "Rules", value: "3 ordered retention rules" },
      { label: "Scope", value: "12 memories" },
    ],
    affectedMemoryIds: [],
  },
  {
    id: "MR-392",
    type: "User request",
    title: "Request MR-392: correct Customer fact 7",
    timestamp: "Yesterday at 14:22",
    status: "Processing",
    statusDetail: "Automatic workflow",
    summary:
      "Dana Whitfield asked ACME Support to correct an outdated billing contact.",
    facts: [
      { label: "Request ID", value: "MR-392" },
      { label: "Submitted by", value: "Dana Whitfield" },
      { label: "Workflow", value: "Processing automatically" },
      { label: "Current use", value: "The inaccurate value is no longer used" },
    ],
    affectedMemoryIds: ["memory-7"],
  },
  {
    id: "R-240722-0200",
    type: "Warning",
    title: "Run R-240722-0200 reached its item limit",
    timestamp: "July 22 at 02:03",
    status: "Incomplete",
    statusDetail: "Later batch required",
    summary:
      "Items beyond the 100,000-item limit were not evaluated in this batch.",
    notice: {
      title: "Later batch required",
      text: "Items beyond the limit, including related memories, will be evaluated in a later batch.",
    },
    facts: [
      { label: "Run ID", value: "R-240722-0200" },
      { label: "Evaluated", value: "100,000 items" },
      { label: "Continuation", value: "Next scheduled batch" },
    ],
    affectedMemoryIds: [],
  },
];

const deliveries: DeliveryRecord[] = [
  {
    eventId: "EVT-1042",
    eventType: "retention.run.completed",
    title: "Retention run completed",
    deliveredAt: "Today at 02:01:08",
    deliveryId: "DEL-8821",
    attempt: "1 of 3",
    destination: "Memory lifecycle destination",
    correlationId: "R-240724-0200",
    relatedActivityId: "R-240724-0200",
    fields: [
      { name: "Agent", value: "ACME Support" },
      { name: "Run", value: "R-240724-0200" },
      { name: "Recorded outcomes", value: "9" },
      { name: "Deleted", value: "6" },
      { name: "Protected", value: "1" },
      { name: "Sent for review", value: "1" },
      { name: "Kept on uncertain signal", value: "1" },
    ],
    privacyNote:
      "9 lifecycle outcomes were delivered on the first attempt. Memory content was excluded.",
  },
  {
    eventId: "EVT-1043",
    eventType: "memory.deletion.refused",
    title: "Legal hold prevented deletion",
    deliveredAt: "Today at 02:01:09",
    deliveryId: "DEL-8822",
    attempt: "1 of 3",
    destination: "Memory lifecycle destination",
    correlationId: "R-240724-0200",
    relatedActivityId: "R-240724-0200",
    affectedMemoryId: "memory-12",
    fields: [
      { name: "Agent", value: "ACME Support" },
      { name: "Run", value: "R-240724-0200" },
      { name: "Memory", value: "Guidance 12" },
      { name: "Control", value: "Remove old conversations" },
      { name: "Outcome", value: "Protected" },
      { name: "Protection", value: "Legal hold" },
    ],
    privacyNote:
      "The delivery identifies the governed memory and outcome. Memory content was excluded.",
  },
  {
    eventId: "EVT-1038",
    eventType: "memory.correction.requested",
    title: "User requested a correction",
    deliveredAt: "Yesterday at 14:22:04",
    deliveryId: "DEL-8817",
    attempt: "1 of 3",
    destination: "Memory lifecycle destination",
    correlationId: "MR-392",
    relatedActivityId: "MR-392",
    affectedMemoryId: "memory-7",
    fields: [
      { name: "Agent", value: "ACME Support" },
      { name: "Request", value: "MR-392" },
      { name: "Memory", value: "Customer fact 7" },
      { name: "Requested by", value: "Dana Whitfield" },
      { name: "Action", value: "Correct" },
    ],
    privacyNote:
      "This delivery started the automatic correction workflow. Memory content was excluded.",
  },
];

function replaceFixtureAgentName<T>(value: T, agentName: string): T {
  if (typeof value === "string") {
    return value.replaceAll("ACME Support", agentName) as T;
  }
  if (Array.isArray(value)) {
    return value.map((item) => replaceFixtureAgentName(item, agentName)) as T;
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        replaceFixtureAgentName(item, agentName),
      ]),
    ) as T;
  }
  return value;
}

export function createComplianceDemoData(
  agentName = "ACME Support",
): ComplianceDemoData {
  return replaceFixtureAgentName({
    memories: structuredClone([...coreMemories, ...createSupplementalMemories()]),
    automations: structuredClone(automations),
    activities: structuredClone(activities),
    deliveries: structuredClone(deliveries),
  }, agentName);
}
