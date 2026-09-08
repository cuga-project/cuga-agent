import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate, useParams } from "react-router-dom";
import { ManageDashboard } from "./ManageDashboard";
import { ManagePage } from "./ManagePage";
import { ChatLanding } from "./ChatLanding";
import { UnauthorizedPage } from "./UnauthorizedPage";
import { AuthProvider, useAuth } from "./AuthContext";
import * as api from "./api";
import * as auth from "./auth";
import "./carbon.scss";

import "./global.css";

// Studio is LAZY on purpose. It is the events control plane, reachable only at /studio and only
// when the events service answers — but a static import put its whole tree in the main chunk, so
// every vanilla CUGA user downloaded it and never used it. React.lazy makes webpack emit it as a
// separate chunk fetched on navigation.
const StudioPage = React.lazy(() =>
  import("./StudioPage").then((m) => ({ default: m.StudioPage }))
);

function RouteRoot({ children }: { children: React.ReactNode }) {
  return <div className="route-root">{children}</div>;
}

// Renders its children only when the events layer is mounted + enabled; otherwise redirects home.
// The nav entries already hide when events is off (via getEventsStatus); this also blocks DIRECT
// navigation to /studio so Studio can never appear in vanilla CUGA (no events service reachable).
function EventsGate({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<"checking" | "on" | "off">("checking");
  useEffect(() => {
    api.getEventsStatus()
      .then((s) => setState(s ? "on" : "off"))
      .catch(() => setState("off"));
  }, []);
  if (state === "checking") return null;                 // don't flash Studio before the check resolves
  if (state === "off") return <Navigate to="/" replace />;
  return <>{children}</>;
}

// Keying by agentId forces a full remount when switching agents from within the chat page
// (same Route pattern, different param — React Router does not remount on its own).
function ChatLandingRoute() {
  const { agentId } = useParams<{ agentId?: string }>();
  return <ChatLanding key={agentId ?? "cuga-default"} />;
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const config = await api.getAuthConfig();
        if (!config.enabled) {
          setReady(true);
          return;
        }
        const params = new URLSearchParams(window.location.search);
        const code = params.get("code");
        const state = params.get("state");
        if (code && state) {
          await auth.handleOidcCallback(code, state);
          if (!cancelled) setReady(true);
          return;
        }
        await auth.checkAuthStatus();
        if (!cancelled) setReady(true);
      } catch {
        if (!cancelled) {
          if (!auth.isLoginInProgress()) {
            auth.markLoginInProgress();
            const base = api.getApiBaseUrl();
            window.location.href = `${base}/auth/login`;
          }
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  if (!ready) return null;
  return <>{children}</>;
}

function RequireRole({ requiredRoles, children }: { requiredRoles: string[]; children: React.ReactNode }) {
  const { user, isLoading, authorizationEnabled } = useAuth();

  if (isLoading) {
    return null; // or a loading spinner
  }

  // If authorization is disabled, allow access regardless of roles
  if (!authorizationEnabled) {
    return <>{children}</>;
  }

  // If auth is disabled, user will be null - allow access
  if (user === null) {
    return <>{children}</>;
  }

  // Check if user has any of the required roles
  const userRoles = user.roles || [];
  const hasRequiredRole = requiredRoles.some(role => userRoles.includes(role));

  if (!hasRequiredRole) {
    // Redirect to unauthorized page if user lacks required role
    return <Navigate to="/unauthorized" replace />;
  }

  return <>{children}</>;
}

function renderApp(): void {
  const rootElement = document.getElementById("root");
  if (!rootElement) {
    throw new Error("Root element with id 'root' not found in index.html");
  }
  const root = createRoot(rootElement);
  root.render(
    <BrowserRouter>
      <AuthGate>
        <AuthProvider>
          <Routes>
            <Route path="/" element={<Navigate to="/chat" replace />} />
            <Route
              path="/manage"
              element={
                <RequireRole requiredRoles={["ServiceOwner", "ServiceAdmin"]}>
                  <RouteRoot><ManageDashboard /></RouteRoot>
                </RequireRole>
              }
            />
            <Route
              path="/manage/:agentId"
              element={
                <RequireRole requiredRoles={["ServiceOwner", "ServiceAdmin"]}>
                  <RouteRoot><ManagePage /></RouteRoot>
                </RequireRole>
              }
            />
            <Route path="/chat" element={<RouteRoot><ChatLandingRoute /></RouteRoot>} />
            <Route path="/chat/:agentId" element={<RouteRoot><ChatLandingRoute /></RouteRoot>} />
            <Route
              path="/studio"
              element={
                <RequireRole requiredRoles={["ServiceOwner", "ServiceAdmin"]}>
                  <EventsGate><RouteRoot>
                    <React.Suspense fallback={null}><StudioPage /></React.Suspense>
                  </RouteRoot></EventsGate>
                </RequireRole>
              }
            />
            <Route path="/unauthorized" element={<RouteRoot><UnauthorizedPage /></RouteRoot>} />
          </Routes>
        </AuthProvider>
      </AuthGate>
    </BrowserRouter>
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderApp);
} else {
  renderApp();
}


