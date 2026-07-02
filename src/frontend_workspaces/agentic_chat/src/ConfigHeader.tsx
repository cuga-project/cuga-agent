import React, { useState, useEffect } from "react";
import { CugaHeader } from "./CugaHeader";
import ToolsConfig from "./ToolsConfig";
import SubAgentsConfig from "./SubAgentsConfig";
import PoliciesConfig from "./PoliciesConfig";
import * as api from "../../frontend/src/api";

interface ConfigHeaderProps {
  onToggleLeftSidebar: () => void;
  onToggleKnowledge: () => void;
  knowledgeDocCount: number;
  // True once the hook has at least one authoritative response (either
  // from localStorage cache or a successful fetch). Used to decide whether
  // to render the "(N)" suffix at all — never show "(0)" or stale data
  // while the count is unknown.
  knowledgeDocsLoaded?: boolean;
  knowledgeEnabled?: boolean | null;
}

interface AgentContext {
  agent_id: string;
  config_version: number | null;
}

export function ConfigHeader({
  onToggleLeftSidebar,
  onToggleKnowledge,
  knowledgeDocCount,
  knowledgeDocsLoaded,
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
          // Three-way decision so the badge never lies:
          //   - Disabled         → "Knowledge"
          //   - Loaded + N>0     → "Knowledge (N)"
          //   - Loaded + N=0     → "Knowledge"   (don't write "(0)")
          //   - Not-yet-loaded   → "Knowledge"   (don't write a stale or guessed number)
          // The "loaded" flag is true after the App-level hook has at least
          // one authoritative response (localStorage cache or fresh fetch),
          // so this also covers the publish window: the previous count is
          // preserved and the user never sees a momentary 0.
          {
            label: knowledgeEnabled !== false && knowledgeDocsLoaded && knowledgeDocCount > 0
              ? `Knowledge (${knowledgeDocCount})`
              : "Knowledge",
            onClick: onToggleKnowledge,
          },
          { label: "Sub Agents", onClick: () => setActiveModal("subagents") },
          { label: "Tools", onClick: () => setActiveModal("tools") },
          { label: "Policies", onClick: () => setActiveModal("policies") },
          { label: "Manage", href: "/manage" },
        ]}
      />

      {activeModal === "subagents" && (
        <SubAgentsConfig onClose={() => setActiveModal(null)} />
      )}
      {activeModal === "tools" && (
        <ToolsConfig onClose={() => setActiveModal(null)} />
      )}
      {activeModal === "policies" && (
        <PoliciesConfig onClose={() => setActiveModal(null)} />
      )}
    </>
  );
}
