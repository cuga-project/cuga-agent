import React, { useState, useEffect } from "react";
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
    fetch("/api/agent/context")
      .then((res) => (res.ok ? res.json() : null))
      .then(
        (data) =>
          data &&
          setAgentContext({
            agent_id: data.agent_id ?? "cuga-default",
            config_version: data.config_version ?? null,
          })
      )
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