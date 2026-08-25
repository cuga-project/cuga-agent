export type ImportedAgentKind = "single" | "supervisor";

export type ParsedImportedSupervisorFields = {
  agentName?: string;
  agentDescription?: string;
  agentKind?: ImportedAgentKind;
  subAgents?: unknown[];
  planApproval?: boolean;
};

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
    out.subAgents = Array.isArray(supervisor.subAgents) ? supervisor.subAgents : [];
    out.planApproval = Boolean(supervisor.planApproval);
  }
  return out;
}
