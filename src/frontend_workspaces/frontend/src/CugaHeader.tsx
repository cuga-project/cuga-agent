import React, { type ReactNode, type ComponentType } from "react";
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
  SideNav,
} from "@carbon/react";
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
}

export function CugaHeader({
  title,
  prefix,
  agentContext,
  navItems = [],
  actions = [],
  linkComponent: LinkComponent,
}: CugaHeaderProps) {
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
            </HeaderGlobalBar>
          </Header>
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