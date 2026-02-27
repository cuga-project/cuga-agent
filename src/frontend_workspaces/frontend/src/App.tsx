import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ManageDashboard } from "./ManageDashboard";
import { ManagePage } from "./ManagePage";
import { CarbonChat } from "./carbon-chat";
import { ChatLanding } from "./ChatLanding";
import * as api from "./api";
import * as auth from "./auth";
import "./carbon.scss";
import "./global.css";

function RouteRoot({ children }: { children: React.ReactNode }) {
  return <div className="route-root">{children}</div>;
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
          const base = api.getApiBaseUrl();
          window.location.href = `${base}/auth/login`;
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

function renderApp(): void {
  const rootElement = document.getElementById("root");
  if (!rootElement) {
    throw new Error("Root element with id 'root' not found in index.html");
  }
  const root = createRoot(rootElement);
  root.render(
    <BrowserRouter>
      <AuthGate>
        <Routes>
          <Route path="/" element={<Navigate to="/chat" replace />} />
          <Route path="/manage" element={<RouteRoot><ManageDashboard /></RouteRoot>} />
          <Route path="/manage/:agentId" element={<RouteRoot><ManagePage /></RouteRoot>} />
          <Route path="/chat" element={<RouteRoot><ChatLanding /></RouteRoot>} />
        </Routes>
      </AuthGate>
    </BrowserRouter>
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderApp);
} else {
  renderApp();
}


