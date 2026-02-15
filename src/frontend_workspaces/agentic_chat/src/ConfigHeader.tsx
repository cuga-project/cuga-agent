import React, { useState, useEffect } from "react";
import { Settings, Sidebar, Folder, BookOpen, Brain, Users, Wrench, Cpu, Shield, UserCog, Menu, X, Sliders } from "lucide-react";
import "./ConfigHeader.css";
import MemoryConfig from "./MemoryConfig";
import KnowledgeConfig from "./KnowledgeConfig";
import ToolsConfig from "./ToolsConfig";
import SubAgentsConfig from "./SubAgentsConfig";
import ModelConfig from "./ModelConfig";
import PoliciesConfig from "./PoliciesConfig";
import AgentHumanConfig from "./AgentHumanConfig";
// import AgentBehaviorConfig from "./AgentBehaviorConfig"; // Temporarily hidden

interface ConfigHeaderProps {
  onToggleLeftSidebar: () => void;
  onToggleWorkspace: () => void;
  leftSidebarCollapsed: boolean;
  workspaceOpen: boolean;
}

export function ConfigHeader({
  onToggleLeftSidebar,
  onToggleWorkspace,
  leftSidebarCollapsed,
  workspaceOpen
}: ConfigHeaderProps) {
  const [activeModal, setActiveModal] = useState<string | null>(null);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 480);
    };

    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);

  const closeMobileMenu = () => setIsMobileMenuOpen(false);
  return (
    <div className="config-header">
      <div className="config-header-left">
        <Settings className="config-header-icon" />
        <span className="config-header-title">CUGA Agent</span>
      </div>
      <div className="config-header-buttons">
        {isMobile ? (
          <button
            className="config-header-btn mobile-menu-btn"
            onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            title="Menu"
          >
            {isMobileMenuOpen ? <X size={16} /> : <Menu size={16} />}
          </button>
        ) : (
          <>
            <button
              className="config-header-btn"
              onClick={onToggleLeftSidebar}
              title={leftSidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
            >
              <Sidebar size={16} />
              <span>Sidebar</span>
            </button>
            <button
              className="config-header-btn"
              onClick={onToggleWorkspace}
              title={workspaceOpen ? "Close workspace" : "Open workspace"}
            >
              <Folder size={16} />
              <span>Workspace</span>
            </button>
            <button
              className="config-header-btn hidden-tab"
              disabled
              title="Configure knowledge sources (Coming soon)"
            >
              <BookOpen size={16} />
              <span>Knowledge</span>
            </button>
            <button
              className="config-header-btn hidden-tab"
              disabled
              title="Configure memory settings (Coming soon)"
            >
              <Brain size={16} />
              <span>Memory</span>
            </button>
            <button
              className="config-header-btn"
              onClick={() => setActiveModal("subagents")}
              title="Configure sub-agents"
            >
              <Users size={16} />
              <span>Sub Agents</span>
            </button>
            <button
              className="config-header-btn"
              onClick={() => setActiveModal("tools")}
              title="Configure tools"
            >
              <Wrench size={16} />
              <span>Tools</span>
            </button>
            <button
              className="config-header-btn hidden-tab"
              disabled
              title="Configure model settings (Coming soon)"
            >
              <Cpu size={16} />
              <span>Model</span>
            </button>
            <button
              className="config-header-btn"
              onClick={() => setActiveModal("policies")}
              title="Configure policies"
            >
              <Shield size={16} />
              <span>Policies</span>
            </button>
            <a
              href="/manage"
              className="config-header-btn"
              title="Manage agent config and versions"
              style={{ textDecoration: "none", color: "inherit" }}
            >
              <Sliders size={16} />
              <span>Manage</span>
            </a>
            <button
              className="config-header-btn hidden-tab"
              disabled
              title="Configure agent autonomy and human interaction (Coming soon)"
            >
              <UserCog size={16} />
              <span>Agent&nbsp;∙&nbsp;Human</span>
            </button>
            {/* Temporarily hidden - Agent Behavior */}
            {/* <button
              className="config-header-btn"
              onClick={() => setActiveModal("behavior")}
              title="Configure agent behavior settings"
            >
              <Activity size={16} />
              <span>Agent Behavior</span>
            </button> */}
          </>
        )}
      </div>

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
      {/* Temporarily hidden - Agent Behavior */}
      {/* {activeModal === "behavior" && (
        <AgentBehaviorConfig onClose={() => setActiveModal(null)} />
      )} */}

      {/* Mobile Menu */}
      {isMobile && isMobileMenuOpen && (
        <div className="mobile-menu-overlay" onClick={closeMobileMenu}>
          <div className="mobile-menu" onClick={(e) => e.stopPropagation()}>
            <div className="mobile-menu-header">
              <h3>Menu</h3>
              <button className="mobile-menu-close" onClick={closeMobileMenu}>
                <X size={20} />
              </button>
            </div>
            <div className="mobile-menu-content">
              <button
                className="mobile-menu-item"
                onClick={() => {
                  onToggleLeftSidebar();
                  closeMobileMenu();
                }}
              >
                <Sidebar size={18} />
                <span>Sidebar</span>
              </button>
              <button
                className="mobile-menu-item"
                onClick={() => {
                  onToggleWorkspace();
                  closeMobileMenu();
                }}
              >
                <Folder size={18} />
                <span>Workspace</span>
              </button>
              <button
                className="mobile-menu-item hidden-tab"
                disabled
              >
                <BookOpen size={18} />
                <span>Knowledge</span>
              </button>
              <button
                className="mobile-menu-item hidden-tab"
                disabled
              >
                <Brain size={18} />
                <span>Memory</span>
              </button>
              <button
                className="mobile-menu-item"
                onClick={() => {
                  setActiveModal("subagents");
                  closeMobileMenu();
                }}
              >
                <Users size={18} />
                <span>Sub Agents</span>
              </button>
              <button
                className="mobile-menu-item"
                onClick={() => {
                  setActiveModal("tools");
                  closeMobileMenu();
                }}
              >
                <Wrench size={18} />
                <span>Tools</span>
              </button>
              <button
                className="mobile-menu-item hidden-tab"
                disabled
              >
                <Cpu size={18} />
                <span>Model</span>
              </button>
              <button
                className="mobile-menu-item"
                onClick={() => {
                  setActiveModal("policies");
                  closeMobileMenu();
                }}
              >
                <Shield size={18} />
                <span>Policies</span>
              </button>
              <button
                className="mobile-menu-item hidden-tab"
                disabled
              >
                <UserCog size={18} />
                <span>Agent ⋅ Human</span>
              </button>
              {/* Temporarily hidden - Agent Behavior */}
              {/* <button
                className="mobile-menu-item"
                onClick={() => {
                  setActiveModal("behavior");
                  closeMobileMenu();
                }}
              >
                <Activity size={18} />
                <span>Agent Behavior</span>
              </button> */}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}



