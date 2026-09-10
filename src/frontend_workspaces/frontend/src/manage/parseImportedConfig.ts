export type ImportedAgentKind = "single" | "supervisor";

export type ParsedImportedSubAgent =
  | { kind: "internal"; ref: string }
  | {
      kind: "a2a";
      name: string;
      endpoint: string;
      auth?: { type: "bearer"; tokenEnvVar?: string };
      timeout?: number;
    };

export type ParsedImportedSupervisorFields = {
  agentName?: string;
  agentDescription?: string;
  agentKind?: ImportedAgentKind;
  subAgents?: ParsedImportedSubAgent[];
  planApproval?: boolean;
};

function parseImportedSubAgent(entry: unknown): ParsedImportedSubAgent | null {
  if (!entry || typeof entry !== "object") return null;
  const item = entry as Record<string, unknown>;
  if (item.kind === "internal" && typeof item.ref === "string" && item.ref) {
    return { kind: "internal", ref: item.ref };
  }
  if (
    item.kind === "a2a" &&
    typeof item.name === "string" &&
    item.name &&
    typeof item.endpoint === "string" &&
    item.endpoint
  ) {
    const parsed: ParsedImportedSubAgent = { kind: "a2a", name: item.name, endpoint: item.endpoint };
    if (item.auth && typeof item.auth === "object") {
      const auth = item.auth as Record<string, unknown>;
      if (auth.type === "bearer") {
        parsed.auth = {
          type: "bearer",
          ...(typeof auth.tokenEnvVar === "string" ? { tokenEnvVar: auth.tokenEnvVar } : {}),
        };
      }
    }
    if (typeof item.timeout === "number") parsed.timeout = item.timeout;
    return parsed;
  }
  return null;
}

export function parseImportedSupervisorFields(
  raw: Record<string, unknown>,
): ParsedImportedSupervisorFields {
  const out: ParsedImportedSupervisorFields = {};
  if (raw.agent && typeof raw.agent === "object") {
    const agent = raw.agent as { name?: unknown; description?: unknown; kind?: unknown };
    if (typeof agent.name === "string" && agent.name) out.agentName = agent.name;
    if (typeof agent.description === "string") out.agentDescription = agent.description;
    if (agent.kind === "single" || agent.kind === "supervisor") out.agentKind = agent.kind;
  }
  if (raw.supervisor && typeof raw.supervisor === "object") {
    const supervisor = raw.supervisor as { subAgents?: unknown; planApproval?: unknown };
    out.subAgents = Array.isArray(supervisor.subAgents)
      ? supervisor.subAgents.flatMap((entry) => {
          const parsed = parseImportedSubAgent(entry);
          return parsed ? [parsed] : [];
        })
      : [];
    if (typeof supervisor.planApproval === "boolean") out.planApproval = supervisor.planApproval;
  }
  return out;
}
