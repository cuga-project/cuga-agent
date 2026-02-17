import React, { useState, useEffect } from "react";
import { CugaHeader } from "./CugaHeader";
import MemoryConfig from "./MemoryConfig";
import KnowledgeConfig from "./KnowledgeConfig";
import ToolsConfig from "./ToolsConfig";
import SubAgentsConfig from "./SubAgentsConfig";
import ModelConfig from "./ModelConfig";
import PoliciesConfig from "./PoliciesConfig";
import AgentHumanConfig from "./AgentHumanConfig";

interface ConfigHeaderProps {
  onToggleLeftSidebar: () => void;
  onToggleWorkspace: () => void;
  leftSidebarCollapsed: boolean;
  workspaceOpen: boolean;
}

interface AgentContext {
  agent_id: string;
  config_version: number | null;
}

export function ConfigHeader({
  onToggleLeftSidebar,
  onToggleWorkspace,
}: ConfigHeaderProps) {
  const [activeModal, setActiveModal] = useState<string | null>(null);
  const [agentContext, setAgentContext] = useState<AgentContext | null>(null);

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
    <>
      <CugaHeader
        title="CUGA Agent"
        agentContext={agentContext}
        navItems={[
          { label: "Sidebar", onClick: onToggleLeftSidebar },
          { label: "Workspace", onClick: onToggleWorkspace },
          { label: "Sub Agents", onClick: () => setActiveModal("subagents") },
          { label: "Tools", onClick: () => setActiveModal("tools") },
          { label: "Policies", onClick: () => setActiveModal("policies") },
          { label: "Manage", href: "/manage" },
        ]}
      />

      {activeModal === "knowledge" && (
        <KnowledgeConfig onClose={() => setActiveModal(null)} />
      )}
      {activeModal === "memory" && (
        <MemoryConfig onClose={() => setActiveModal(null)} />
      )}
      {activeModal === "subagents" && (
        <SubAgentsConfig onClose={() => setActiveModal(null)} />
      )}
      {activeModal === "tools" && (
        <ToolsConfig onClose={() => setActiveModal(null)} />
      )}
      {activeModal === "model" && (
        <ModelConfig onClose={() => setActiveModal(null)} />
      )}
      {activeModal === "policies" && (
        <PoliciesConfig onClose={() => setActiveModal(null)} />
      )}
      {activeModal === "agenthuman" && (
        <AgentHumanConfig onClose={() => setActiveModal(null)} />
      )}
    </>
  );
}
