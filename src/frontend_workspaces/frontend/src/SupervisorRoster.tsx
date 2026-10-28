import React from "react";
import { Link } from "react-router-dom";
import { Tag } from "@carbon/react";
import { Bot, Flow } from "@carbon/icons-react";

export interface SupervisorMember {
  kind: "internal" | "a2a";
  ref?: string;
  name?: string;
  endpoint?: string;
}

export interface RosterAgent {
  id: string;
  name?: string;
  description?: string;
  kind?: "single" | "supervisor";
}

export function SupervisorRoster({ members, agents }: { members: SupervisorMember[]; agents: RosterAgent[] }) {
  return (
    <section aria-label="Supervised agents" style={{ padding: "1rem" }}>
      {members.length === 0 ? <p>No agents are assigned to this supervisor.</p> : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {members.map((member, index) => {
            const agent = member.kind === "internal" ? agents.find((a) => a.id === member.ref) : undefined;
            const name = agent?.name?.trim() || member.name || member.ref || "Unknown agent";
            const Icon = agent?.kind === "supervisor" ? Flow : Bot;
            return (
              <li key={`${member.kind}:${member.ref || member.name}:${index}`} style={{ padding: "0.875rem 0", borderBottom: "1px solid var(--cds-border-subtle-01)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
                  <Icon size={18} aria-hidden="true" />
                  <strong>{name}</strong>
                  <Tag type={member.kind === "a2a" ? "blue" : "purple"} size="sm">{member.kind === "a2a" ? "External A2A" : "Agent"}</Tag>
                </div>
                {member.ref && <code style={{ display: "block", marginTop: "0.375rem", fontSize: "0.75rem" }}>{member.ref}</code>}
                {agent?.description && <p style={{ fontSize: "0.75rem", marginTop: "0.375rem", color: "var(--cds-text-secondary)" }}>{agent.description}</p>}
                {member.kind === "internal" && member.ref && (
                  agents.length > 0 && !agent ? <p role="status">Agent unavailable. Update the supervisor configuration.</p> :
                    <Link to={`/chat/${encodeURIComponent(member.ref)}`} style={{ display: "inline-block", marginTop: "0.5rem", fontSize: "0.75rem" }}>Chat with {name}</Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
