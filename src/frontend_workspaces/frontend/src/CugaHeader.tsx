import React, { useState, useEffect, useRef, type ReactNode, type ComponentType } from "react";
import {
  Header,
  HeaderName,
  HeaderNavigation,
  HeaderMenuItem,
  HeaderGlobalBar,
  HeaderGlobalAction,
  HeaderPanel,
  HeaderMenuButton,
  SideNav,
  SideNavItems,
  SideNavLink,
  Theme,
  Button,
} from "@carbon/react";
import { Logout, Password, User } from "@carbon/icons-react";
import { useAuth } from "./AuthContext";
import * as api from "./api";
import * as auth from "./auth";
import "./CugaHeader.css";

export interface CugaHeaderNavItem {
  label: string;
  href?: string;
  to?: string;
  onClick?: () => void;
  current?: boolean;
}

export interface CugaHeaderAction {
  icon: ReactNode;
  label: string;
  href?: string;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
  expanded?: boolean;
  controls?: string;
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
  roles?: string[];
}

function getInitials(name?: string, email?: string): string | null {
  if (name) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return parts[0].slice(0, 2).toUpperCase();
  }
  if (email) return email[0].toUpperCase();
  return null;
}

export function CugaHeader({
  title,
  prefix,
  agentContext,
  navItems,
  actions = [],
  linkComponent: LinkComponent,
  onOpenSecrets,
}: CugaHeaderProps) {
  const { user: userInfo, isLoading: authLoading, authorizationEnabled } = useAuth();
  const pathname = window.location.pathname;
  const [navigationOpen, setNavigationOpen] = useState(false);
  const navigationRef = useRef<HTMLElement>(null);
  const profileButtonRef = useRef<HTMLButtonElement>(null);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [userPanelOpen, setUserPanelOpen] = useState(false);
  const [hideLogo, setHideLogo] = useState(true);
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [studioEnabled, setStudioEnabled] = useState(false);
  const [brandName, setBrandName] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!userPanelOpen) return;
    const handler = (e: MouseEvent) => {
      const el = e.target as Node;
      if (panelRef.current && !panelRef.current.contains(el) && !profileButtonRef.current?.contains(el)) {
        setUserPanelOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [userPanelOpen]);

  useEffect(() => {
    api.getEventsStatus().then((status) => setStudioEnabled(!!status)).catch(() => {});
    api.getAuthConfig().then((c) => {
      setAuthEnabled(c.enabled);
    }).catch(() => {});

    api.getUiConfig().then((c) => {
      setHideLogo(c.hide_cuga_logo);
      setMemoryEnabled(!!c.evolve_memory_enabled);
      setBrandName(c.brand_name);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!userPanelOpen) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setUserPanelOpen(false);
        profileButtonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [userPanelOpen]);

  const canManage = !authLoading && (!authorizationEnabled || userInfo === null || userInfo.canManage === true);
  const routeAgentId = /^\/(?:chat|manage)\/([^/]+)/.exec(pathname)?.[1];
  const activeAgentId = routeAgentId ?? (agentContext?.agent_id ? encodeURIComponent(agentContext.agent_id) : undefined);
  const chatPath = activeAgentId && activeAgentId !== "cuga-default" ? `/chat/${activeAgentId}` : "/chat";
  const inMemory = pathname.startsWith("/chat") && new URLSearchParams(window.location.search).get("view") === "memory";
  const productNav: CugaHeaderNavItem[] = [
    { label: "Chat", to: chatPath, current: pathname.startsWith("/chat") && !inMemory },
    ...(canManage ? [{ label: "Agents", to: "/manage", current: pathname.startsWith("/manage") }] : []),
    ...(memoryEnabled ? [{ label: "Memory", to: `${chatPath}?view=memory`, current: inMemory }] : []),
    ...(canManage && studioEnabled ? [{ label: "Studio", to: "/studio", current: pathname === "/studio" }] : []),
  ];
  const navigationItems = navItems ?? productNav;
  const isCurrent = (item: CugaHeaderNavItem) => {
    if (item.current !== undefined) return item.current;
    const target = item.to ?? item.href;
    if (!target) return false;
    const targetPath = new URL(target, window.location.origin).pathname;
    return pathname === targetPath || (targetPath !== "/" && pathname.startsWith(`${targetPath}/`));
  };

  useEffect(() => {
    if (navigationOpen) navigationRef.current?.querySelector<HTMLAnchorElement>("a")?.focus();
  }, [navigationOpen]);

  const displayTitle = brandName ?? title;

  const displayName = userInfo?.name ?? "";
  const displayEmail = userInfo?.email ?? userInfo?.sub ?? "";
  const initials = getInitials(userInfo?.name, userInfo?.email ?? userInfo?.sub);

  const renderNavItem = (item: CugaHeaderNavItem, onItemClick?: () => void) => {
    const content = item.label;
    if (item.to && LinkComponent) {
      return (
        <HeaderMenuItem isCurrentPage={isCurrent(item)} key={item.label} as={LinkComponent as any} to={item.to} onClick={onItemClick}>
          {content}
        </HeaderMenuItem>
      );
    }
    if ((item.href || item.to) && !item.onClick) {
      return (
        <HeaderMenuItem isCurrentPage={isCurrent(item)} key={item.label} href={item.href ?? item.to} onClick={onItemClick}>
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
    <Theme theme="g100" className="cuga-header-wrapper">
      <Header aria-label="CUGA">
        {navigationItems.length > 0 && <HeaderMenuButton
          id="cuga-navigation-toggle"
          aria-label={navigationOpen ? "Close navigation" : "Open navigation"}
          isActive={navigationOpen}
          onClick={() => setNavigationOpen((open) => !open)}
        />}
        {!hideLogo && (
          <a href="/" className="cuga-header-logo" aria-label="Home">
            <img src="https://avatars.githubusercontent.com/u/230847519?s=200&v=4" alt="" />
          </a>
        )}
        <HeaderName href="/" prefix={prefix ?? ""}>
          {displayTitle}
        </HeaderName>
        <HeaderNavigation aria-label="CUGA">
          {navigationItems.map((item) => renderNavItem(item))}
        </HeaderNavigation>
        <HeaderGlobalBar>
          {actions.map((action) => {
            if (action.href && !action.onClick) {
              return (
                <a
                  key={action.label}
                  href={action.href}
                  className={`cds--header__global-action ${action.className ?? ""}`.trim()}
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
                    aria-expanded={action.expanded}
                    aria-controls={action.controls}
                    onClick={action.onClick}
                    {...{ disabled: action.disabled }}
                    className={action.className}
                  >
                    {action.icon}
                  </HeaderGlobalAction>
                );
              })}
              {!authEnabled && onOpenSecrets && (
                <HeaderGlobalAction
                  aria-label="Manage Secrets"
                  onClick={onOpenSecrets}
                >
                  <Password size={20} />
                </HeaderGlobalAction>
              )}
              <HeaderGlobalAction
                ref={profileButtonRef}
                aria-controls="cuga-user-panel"
                aria-label="User profile"
                isActive={userPanelOpen}
                aria-expanded={userPanelOpen}
                onClick={() => setUserPanelOpen((o) => !o)}
                className="cuga-user-avatar-btn"
              >
                {initials ? (
                  <span className="cuga-user-avatar-initials">{initials}</span>
                ) : (
                  <span className="cuga-user-avatar-empty">
                    <User size={20} />
                  </span>
                )}
              </HeaderGlobalAction>
            </HeaderGlobalBar>
        {navigationItems.length > 0 && navigationOpen && <SideNav
          ref={navigationRef}
          aria-label="CUGA navigation"
          expanded={navigationOpen}
          isPersistent={false}
          onToggle={(_event, expanded) => {
            setNavigationOpen(expanded);
            if (!expanded) document.getElementById("cuga-navigation-toggle")?.focus();
          }}
          onOverlayClick={() => setNavigationOpen(false)}
          onSideNavBlur={() => setNavigationOpen(false)}
        >
          <SideNavItems>
            {navigationItems.map((item) => <SideNavLink
              key={item.label}
              {...(LinkComponent && item.to ? { as: LinkComponent as any, to: item.to } : { href: item.to ?? item.href ?? "#" })}
              isActive={isCurrent(item)}
              onClick={(event: React.MouseEvent<HTMLAnchorElement>) => {
                if (item.onClick) { event.preventDefault(); item.onClick(); }
                setNavigationOpen(false);
              }}
            >{item.label}</SideNavLink>)}
          </SideNavItems>
        </SideNav>}
          </Header>
          <div id="cuga-user-panel" ref={panelRef} hidden={!userPanelOpen} className="cuga-user-panel-wrapper">
            <HeaderPanel expanded={userPanelOpen} aria-label="User profile">
              <div className="cuga-user-panel">
                <div className="cuga-user-panel-header">
                  <div className="cuga-user-panel-avatar">
                    {initials ? (
                      <span className="cuga-user-panel-avatar-initials">{initials}</span>
                    ) : (
                      <span className="cuga-user-panel-avatar-icon">
                        <User size={16} />
                      </span>
                    )}
                  </div>
                  <div className="cuga-user-panel-details">
                    {authEnabled ? (
                      <>
                        {displayName && <p className="cuga-user-panel-name">{displayName}</p>}
                        {displayEmail && <p className="cuga-user-panel-email">{displayEmail}</p>}
                      </>
                    ) : (
                      <p className="cuga-user-panel-name">Guest</p>
                    )}
                    {agentContext && (
                      <p className="cuga-user-panel-agent">
                        {agentContext.agent_id}
                        {agentContext.config_version != null ? ` · v${agentContext.config_version}` : ""}
                      </p>
                    )}
                  </div>
                </div>
                <ul className="cuga-user-menu-list">
                  {onOpenSecrets && (
                    <li>
                      <Button
                        kind="ghost"
                        className="cuga-user-menu-item"
                        onClick={() => { onOpenSecrets(); setUserPanelOpen(false); }}
                      >
                        <Password size={16} />
                        Manage Secrets
                      </Button>
                    </li>
                  )}
                  {authEnabled && (
                    <li>
                      <Button
                        kind="ghost"
                        className="cuga-user-menu-item"
                        onClick={() => auth.logout()}
                      >
                        <Logout size={16} />
                        Sign out
                      </Button>
                    </li>
                  )}
                </ul>
              </div>
            </HeaderPanel>
          </div>
        </Theme>
  );
}
