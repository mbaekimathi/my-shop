/**
 * When a shop/employee session expires, leave the page and open login.
 */

let redirecting = false;

function authRoot() {
  return document.querySelector("[data-workspace][data-auth-required]");
}

function fallbackLoginUrl() {
  const root = authRoot();
  return root?.getAttribute("data-login-url") || "/employees/login/";
}

export function redirectToLogin(loginUrl) {
  if (redirecting) return;
  const target = String(loginUrl || fallbackLoginUrl() || "").trim();
  if (!target) return;
  redirecting = true;
  window.location.replace(target);
}

function isSessionExpiredPayload(data) {
  if (!data || typeof data !== "object") return false;
  const error = String(data.error || "").toLowerCase();
  return (
    error === "session_expired" ||
    error === "auth_required" ||
    error.includes("session expired")
  );
}

export function handleSessionPayload(data, status) {
  if (!isSessionExpiredPayload(data)) return false;
  redirectToLogin(data.login_url || fallbackLoginUrl());
  return true;
}

function expectedAuthMode() {
  return authRoot()?.getAttribute("data-auth-mode") || "";
}

function authStillValid(auth) {
  const expected = expectedAuthMode();
  if (!expected) return true;
  if (expected === "shop") return auth === "shop";
  if (expected === "employee") return auth === "employee";
  return auth && auth !== "none";
}

function onPingDetail(data) {
  if (!authRoot() || !data || data.ok === false) return;
  if (authStillValid(data.auth)) return;
  redirectToLogin(data.login_url || fallbackLoginUrl());
}

function installFetchGuard() {
  if (typeof window.fetch !== "function" || window.fetch.__myshopSessionGuard) {
    return;
  }
  const originalFetch = window.fetch.bind(window);
  const wrapped = async (...args) => {
    const response = await originalFetch(...args);
    if (
      authRoot() &&
      (response.status === 401 || response.status === 403)
    ) {
      const contentType = response.headers.get("content-type") || "";
      if (contentType.includes("application/json")) {
        try {
          const data = await response.clone().json();
          handleSessionPayload(data, response.status);
        } catch (_err) {
          /* ignore non-json */
        }
      }
    }
    return response;
  };
  wrapped.__myshopSessionGuard = true;
  window.fetch = wrapped;
}

export function initSessionGuard() {
  if (!authRoot()) return;
  installFetchGuard();
  document.addEventListener("myshop:ping", (event) => {
    onPingDetail(event.detail);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initSessionGuard, { once: true });
} else {
  initSessionGuard();
}
