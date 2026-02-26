import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ManageDashboard } from "./ManageDashboard";
import { ManagePage } from "./ManagePage";
import { CarbonChat } from "./carbon-chat";
import { ChatLanding } from "./ChatLanding";
import "./carbon.scss";
import "./global.css";

function RouteRoot({ children }: { children: React.ReactNode }) {
  return <div className="route-root">{children}</div>;
}

function renderApp(): void {
  const rootElement = document.getElementById("root");
  if (!rootElement) {
    throw new Error("Root element with id 'root' not found in index.html");
  }
  const root = createRoot(rootElement);
  root.render(
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/manage" element={<RouteRoot><ManageDashboard /></RouteRoot>} />
        <Route path="/manage/:agentId" element={<RouteRoot><ManagePage /></RouteRoot>} />
        {/* <Route path="/chat" element={<RouteRoot><CarbonChat /></RouteRoot>} /> */}
        <Route path="/chat" element={<RouteRoot><ChatLanding /></RouteRoot>} />
      </Routes>
    </BrowserRouter>
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderApp);
} else {
  renderApp();
}


