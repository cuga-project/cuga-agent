import React, { useState, useEffect } from "react";
import { CugaHeader } from "./CugaHeader";
import MemoryConfig from "./MemoryConfig";
import KnowledgeConfig from "./KnowledgeConfig";
import ToolsConfig from "./ToolsConfig";
import SubAgentsConfig from "./SubAgentsConfig";
import ModelConfig from "./ModelConfig";
import PoliciesConfig from "./PoliciesConfig";
import AgentHumanConfig from "./AgentHumanConfig";
import * as api from "../../frontend/src/api";

interface ConfigHeaderProps {
  onToggleLeftSidebar: () => void;
  onToggleWorkspace: () => void;
  onToggleKnowledge: () => void;
  leftSidebarCollapsed: boolean;
  workspaceOpen: boolean;
  knowledgeOpen: boolean;
  knowledgeDocCount: number;
  knowledgeEnabled?: boolean | null;
}

interface AgentContext {
  agent_id: string;
  config_version: number | null;
}

export function ConfigHeader({
  onToggleLeftSidebar,
  onToggleWorkspace,
  onToggleKnowledge,
  knowledgeOpen,
  knowledgeDocCount,
  knowledgeEnabled,
}: ConfigHeaderProps) {
  const [activeModal, setActiveModal] = useState<string | null>(null);
  const [agentContext, setAgentContext] = useState<AgentContext | null>(null);

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
    <>
      <CugaHeader
        title="CUGA Agent"
        agentContext={agentContext}
        navItems={[
          { label: "Sidebar", onClick: onToggleLeftSidebar },
          { label: "Workspace", onClick: onToggleWorkspace },
          { label: knowledgeEnabled !== false && knowledgeDocCount > 0 ? `Knowledge (${knowledgeDocCount})` : "Knowledge", onClick: onToggleKnowledge },
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
