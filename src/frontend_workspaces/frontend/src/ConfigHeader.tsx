import React from "react";
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
  return (
    <CugaHeader
      title="CUGA Agent"
      navItems={[
        { label: "Sidebar", onClick: onToggleLeftSidebar },
        { label: "Workspace", onClick: onToggleWorkspace },
        { label: "Manage", href: "/manage" },
      ]}
    />
  );
}