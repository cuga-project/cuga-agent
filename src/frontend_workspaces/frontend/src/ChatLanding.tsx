import React, { useState } from "react";
import { ConfigHeader } from "../../agentic_chat/src/ConfigHeader";
import CarbonChat from "./carbon-chat/CarbonChat";
import "./ChatLanding.css";

export function ChatLanding() {
  const [leftSidebarCollapsed, setLeftSidebarCollapsed] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);

  const handleToggleLeftSidebar = () => {
    setLeftSidebarCollapsed(!leftSidebarCollapsed);
  };

  const handleToggleWorkspace = () => {
    setWorkspaceOpen(!workspaceOpen);
  };

  return (
    <div className="chat-landing">
      <ConfigHeader
        onToggleLeftSidebar={handleToggleLeftSidebar}
        onToggleWorkspace={handleToggleWorkspace}
        leftSidebarCollapsed={leftSidebarCollapsed}
        workspaceOpen={workspaceOpen}
      />
      <div className="chat-landing-body">
        <CarbonChat contained={true} />
      </div>
    </div>
  );
}