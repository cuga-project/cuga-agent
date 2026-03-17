/*
 * OIDC auth helpers: callback handling and session check.
 */

import * as api from "./api";

export async function handleOidcCallback(code: string, state: string): Promise<void> {
  const res = await api.postAuthCallback(code, state);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Auth callback failed: ${res.status} ${text}`);
  }
  const url = new URL(window.location.href);
  url.searchParams.delete("code");
  url.searchParams.delete("state");
  window.history.replaceState({}, "", url.pathname + url.search);
}

export async function checkAuthStatus(): Promise<void> {
  const res = await api.getAgentContext();
  if (res.status === 401) throw new Error("Not authenticated");
}

export async function logout(): Promise<void> {
  const res = await api.postAuthLogout();
  let endSessionUrl: string | null = null;
  try {
    const data = await res.json();
    endSessionUrl = data?.end_session_url ?? null;
  } catch {
    // ignore
  }
  if (endSessionUrl) {
    window.location.href = endSessionUrl;
  } else {
    window.location.href = "/";
  }
}
