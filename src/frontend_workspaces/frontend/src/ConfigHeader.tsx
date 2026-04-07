import React, { useState, useEffect } from "react";
import * as api from "./api";
import { CugaHeader } from "./CugaHeader";

interface ConfigHeaderProps {
  onToggleLeftSidebar: () => void;
  onToggleWorkspace: () => void;
  leftSidebarCollapsed: boolean;
  workspaceOpen: boolean;
}

export function ConfigHeader({
  onToggleLeftSidebar,
  onToggleWorkspace,
}: ConfigHeaderProps) {
  const [agentContext, setAgentContext] = useState<{ agent_id: string; config_version: number | null } | null>(null);

  useEffect(() => {
    api.getAgentContext()
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) {
          const agentId = data.agent_id ?? "cuga-default";
          setAgentContext({
            agent_id: agentId,
            config_version: data.config_version ?? null,
          });
          // Set agent ID for knowledge API calls
          api.setKnowledgeAgentId(agentId);
        }
      })
      .catch(() => {});
  }, []);

  return (
    <CugaHeader
      title="CUGA Agent"
      agentContext={agentContext ?? undefined}
      navItems={[
        { label: "Conversations", onClick: onToggleLeftSidebar },
        { label: "Agent Config", onClick: onToggleWorkspace },
        { label: "Manage", href: "/manage" },
      ]}
    />
  );
}
