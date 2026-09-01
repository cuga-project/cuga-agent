import React, { useState, useEffect } from "react";
import { DataBase } from "@carbon/icons-react";
import * as api from "./api";
import { CugaHeader } from "./CugaHeader";

interface ConfigHeaderProps {
  onToggleLeftSidebar: () => void;
  onToggleWorkspace: () => void;
  onOpenMemory?: () => void;
  // When set (e.g. from a /chat/:agentId route), this agent is already the source of truth —
  // skip the global /api/agent/context fetch below, which always reports the single default
  // agent and would otherwise clobber api.setKnowledgeAgentId back to "cuga-default".
  agentId?: string;
}

export function ConfigHeader({
  onToggleLeftSidebar,
  onToggleWorkspace,
  onOpenMemory,
  agentId,
}: ConfigHeaderProps) {
  const [agentContext, setAgentContext] = useState<{ agent_id: string; config_version: number | null } | null>(
    agentId ? { agent_id: agentId, config_version: null } : null
  );

  useEffect(() => {
    if (agentId) return;
    api.getAgentContext()
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) {
          const resolvedAgentId = data.agent_id ?? "cuga-default";
          setAgentContext({
            agent_id: resolvedAgentId,
            config_version: data.config_version ?? null,
          });
          // Set agent ID for knowledge API calls
          api.setKnowledgeAgentId(resolvedAgentId);
        }
      })
      .catch(() => {});
  }, [agentId]);

  return (
    <CugaHeader
      title="CUGA Agent"
      agentContext={agentContext ?? undefined}
      navItems={[
        { label: "Conversations", onClick: onToggleLeftSidebar },
        { label: "Agent Config", onClick: onToggleWorkspace },
        ...(onOpenMemory ? [{ label: "Memory", onClick: onOpenMemory }] : []),
        { label: "Manage", href: "/manage" },
      ]}
      actions={
        onOpenMemory
          ? [{
              icon: <DataBase size={20} />,
              label: "Memory",
              onClick: onOpenMemory,
              className: "cuga-memory-mobile-action",
            }]
          : []
      }
    />
  );
}
