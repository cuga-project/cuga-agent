import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { App } from "agentic_chat";
import { ManageDashboard } from "./ManageDashboard";
import { ManagePage } from "./ManagePage";

function renderApp(): void {
  const rootElement = document.getElementById("root");
  if (!rootElement) {
    throw new Error("Root element with id 'root' not found in index.html");
  }
  const root = createRoot(rootElement);
  root.render(
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/manage" element={<ManageDashboard />} />
        <Route path="/manage/:agentId" element={<ManagePage />} />
      </Routes>
    </BrowserRouter>
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderApp);
} else {
  renderApp();
}


