import React, { useState, useEffect, useRef, type ReactNode, type ComponentType } from "react";
import {
  Header,
  HeaderContainer,
  HeaderName,
  HeaderNavigation,
  HeaderMenuItem,
  HeaderGlobalBar,
  HeaderGlobalAction,
  HeaderMenuButton,
  HeaderSideNavItems,
  HeaderPanel,
  Switcher,
  SwitcherItem,
  SwitcherDivider,
  SideNav,
} from "@carbon/react";
import { Logout, Password } from "@carbon/icons-react";
import * as api from "../../frontend/src/api";
import * as auth from "../../frontend/src/auth";
import "./CugaHeader.css";

export interface CugaHeaderNavItem {
  label: string;
  href?: string;
  to?: string;
  onClick?: () => void;
}

export interface CugaHeaderAction {
  icon: ReactNode;
  label: string;
  href?: string;
  onClick?: () => void;
  disabled?: boolean;
}

export interface CugaHeaderProps {
  title: string;
  prefix?: string;
  agentContext?: { agent_id: string; config_version: number | null };
  navItems?: CugaHeaderNavItem[];
  actions?: CugaHeaderAction[];
  linkComponent?: ComponentType<{ href?: string; to?: string; children?: ReactNode; className?: string; onClick?: () => void }>;
  onOpenSecrets?: () => void;
}

interface UserInfo {
  name?: string;
  email?: string;
  sub?: string;
}

function getInitials(name?: string, email?: string): string {
  if (name) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return parts[0].slice(0, 2).toUpperCase();
  }
  if (email) return email[0].toUpperCase();
  return "?";
}

export function CugaHeader({
  title,
  prefix,
  agentContext,
  navItems = [],
  actions = [],
  linkComponent: LinkComponent,
  onOpenSecrets,
}: CugaHeaderProps) {
  const [authEnabled, setAuthEnabled] = useState(false);
  const [userPanelOpen, setUserPanelOpen] = useState(false);
  const [userInfo, setUserInfo] = useState<UserInfo | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.getAuthConfig().then((c) => {
      setAuthEnabled(c.enabled);
      if (c.enabled) {
        api.apiFetch("/auth/userinfo")
          .then((r) => r.ok ? r.json() : null)
          .then((d) => d && setUserInfo({ name: d.name, email: d.email, sub: d.sub }))
          .catch(() => {});
      }
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!userPanelOpen) return;
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setUserPanelOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [userPanelOpen]);

  const displayName = userInfo?.name ?? "";
  const displayEmail = userInfo?.email ?? userInfo?.sub ?? "";
  const initials = getInitials(userInfo?.name, userInfo?.email ?? userInfo?.sub);

  const renderNavItem = (item: CugaHeaderNavItem, onItemClick?: () => void) => {
    const content = item.label;
    if (item.to && LinkComponent) {
      return (
        <HeaderMenuItem key={item.label} as={LinkComponent as any} to={item.to} onClick={onItemClick}>
          {content}
        </HeaderMenuItem>
      );
    }
    if (item.href && !item.onClick) {
      return (
        <HeaderMenuItem key={item.label} href={item.href} onClick={onItemClick}>
          {content}
        </HeaderMenuItem>
      );
    }
    return (
      <HeaderMenuItem
        key={item.label}
        href="#"
        onClick={(e) => { e.preventDefault(); item.onClick?.(); onItemClick?.(); }}
      >
        {content}
      </HeaderMenuItem>
    );
  };

  return (
    <HeaderContainer
      render={({ isSideNavExpanded, onClickSideNavExpand }) => (
        <div className="cuga-header-wrapper">
          <Header aria-label="CUGA">
            <HeaderMenuButton
              aria-label="Open menu"
              isActive={isSideNavExpanded}
              onClick={onClickSideNavExpand}
              isCollapsible
            />
            <a href="/" className="cuga-header-logo" aria-label="Home">
              <img src="https://avatars.githubusercontent.com/u/230847519?s=200&v=4" alt="" />
            </a>
            <HeaderName href="/" prefix={prefix ?? ""}>
              {title}
            </HeaderName>
            {agentContext && (
              <span className="cuga-header-agent-context" title={`Config v${agentContext.config_version ?? "—"}`}>
                {agentContext.agent_id}
                {agentContext.config_version != null ? ` · v${agentContext.config_version}` : ""}
              </span>
            )}
            <HeaderNavigation aria-label="CUGA">
              {navItems.map((item) => renderNavItem(item))}
            </HeaderNavigation>
            <HeaderGlobalBar>
              {actions.map((action) => {
                if (action.href && !action.onClick) {
                  return (
                    <a
                      key={action.label}
                      href={action.href}
                      className="cds--header__global-action"
                      aria-label={action.label}
                      title={action.label}
                      style={{ display: "flex", alignItems: "center", padding: "0 1rem", color: "inherit", textDecoration: "none" }}
                    >
                      {action.icon}
                    </a>
                  );
                }
                return (
                  <HeaderGlobalAction
                    key={action.label}
                    aria-label={action.label}
                    title={action.label}
                    onClick={action.onClick}
                    disabled={action.disabled}
                  >
                    {action.icon}
                  </HeaderGlobalAction>
                );
              })}
              {!authEnabled && onOpenSecrets && (
                <HeaderGlobalAction
                  aria-label="Manage Secrets"
                  title="Manage Secrets"
                  onClick={onOpenSecrets}
                >
                  <Password size={20} />
                </HeaderGlobalAction>
              )}
              {authEnabled && (
                <HeaderGlobalAction
                  aria-label="User profile"
                  title={displayEmail || displayName || "User profile"}
                  isActive={userPanelOpen}
                  onClick={() => setUserPanelOpen((o) => !o)}
                  className="cuga-user-avatar-btn"
                >
                  <span className="cuga-user-avatar-initials">{initials}</span>
                </HeaderGlobalAction>
              )}
            </HeaderGlobalBar>
          </Header>
          <div ref={panelRef}>
            <HeaderPanel expanded={userPanelOpen} aria-label="User panel">
              <Switcher aria-label="User panel">
                <div className="cuga-user-panel-info">
                  <div className="cuga-user-panel-avatar">
                    <span className="cuga-user-panel-avatar-initials">{initials}</span>
                  </div>
                  <div className="cuga-user-panel-details">
                    {displayName && <p className="cuga-user-panel-name">{displayName}</p>}
                    {displayEmail && <p className="cuga-user-panel-email">{displayEmail}</p>}
                  </div>
                </div>
                {onOpenSecrets && (
                  <>
                    <SwitcherItem
                      aria-label="Manage Secrets"
                      onClick={() => { onOpenSecrets(); setUserPanelOpen(false); }}
                    >
                      <Password size={16} style={{ marginRight: "0.5rem", flexShrink: 0 }} />
                      Manage Secrets
                    </SwitcherItem>
                    <SwitcherDivider />
                  </>
                )}
                <SwitcherDivider />
                <SwitcherItem aria-label="Sign out" onClick={() => auth.logout()}>
                  <Logout size={16} style={{ marginRight: "0.5rem", flexShrink: 0 }} />
                  Sign out
                </SwitcherItem>
              </Switcher>
            </HeaderPanel>
          </div>
          {isSideNavExpanded && (
            <SideNav
              aria-label="Side navigation"
              expanded
              isChildOfHeader
              onOverlayClick={onClickSideNavExpand}
              onToggle={(_, expanded) => { if (!expanded) onClickSideNavExpand(); }}
            >
              <HeaderSideNavItems hasDivider>
                {navItems.map((item) => renderNavItem(item, onClickSideNavExpand))}
              </HeaderSideNavItems>
            </SideNav>
          )}
        </div>
      )}
    />
  );
}
