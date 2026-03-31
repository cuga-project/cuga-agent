/*
 * OIDC auth helpers: callback handling and session check.
 */

import * as api from "./api";

const LOGIN_IN_PROGRESS_KEY = "cuga_login_in_progress";

export function markLoginInProgress(): void {
  sessionStorage.setItem(LOGIN_IN_PROGRESS_KEY, "1");
}

export function clearLoginInProgress(): void {
  sessionStorage.removeItem(LOGIN_IN_PROGRESS_KEY);
}

export function isLoginInProgress(): boolean {
  return sessionStorage.getItem(LOGIN_IN_PROGRESS_KEY) === "1";
}

export async function handleOidcCallback(code: string, state: string): Promise<void> {
  const res = await api.postAuthCallback(code, state);
  clearLoginInProgress();
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
  const base = api.getApiBaseUrl();
  const res = await fetch(`${base}/auth/userinfo`, { credentials: "include" });
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
