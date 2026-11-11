/**
 * Session expiry redirect for workspace pages.
 *
 * Employee and shop sessions may run in many tabs and devices at once.
 * Same-origin fetches are stamped with the elapsed time since the last real
 * interaction (X-MyShop-Activity) so the server-side idle timeout can tell
 * user activity apart from background polls — polls may detect expiry but
 * can never postpone it.
 */

let redirecting = false;
// One bad ping can be a rotation race; require consecutive misses before bounce.
let consecutiveAuthFailures = 0;
const AUTH_FAILURE_THRESHOLD = 2;

const ACTIVITY_HEADER = "X-MyShop-Activity";
const ACTIVITY_EVENTS = [
  "pointerdown",
  "pointermove",
  "keydown",
  "touchstart",
  "wheel",
  "scroll",
];
let lastUserActivityAt = Date.now();
let activityTrackerInstalled = false;

function noteUserActivity() {
  lastUserActivityAt = Date.now();
}

function installActivityTracker() {
  if (activityTrackerInstalled) return;
  activityTrackerInstalled = true;
  ACTIVITY_EVENTS.forEach((name) => {
    window.addEventListener(name, noteUserActivity, {
      passive: true,
      capture: true,
    });
  });
}

function withActivityStamp(args) {
  const [input, init] = args;
  try {
    const isRequest =
      typeof Request !== "undefined" && input instanceof Request;
    const rawUrl = isRequest ? input.url : String(input);
    if (
      new URL(rawUrl, window.location.origin).origin !==
      window.location.origin
    ) {
      return args;
    }
    const headers = new Headers(isRequest ? input.headers : init?.headers);
    headers.set(ACTIVITY_HEADER, String(Date.now() - lastUserActivityAt));
    if (isRequest) {
      return [new Request(input, { headers }), init];
    }
    return [input, { ...(init || {}), headers }];
  } catch (_err) {
    return args;
  }
}

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

function authStillValid(data) {
  const expected = expectedAuthMode();
  if (!expected) return true;
  if (expected === "shop") {
    if (typeof data.shop_auth === "boolean") return data.shop_auth;
    return data.auth === "shop";
  }
  if (expected === "employee") {
    if (typeof data.employee_auth === "boolean") return data.employee_auth;
    return data.auth === "employee";
  }
  return data.auth && data.auth !== "none";
}

function onPingDetail(data) {
  if (!authRoot() || !data || data.ok === false) return;
  if (authStillValid(data)) {
    consecutiveAuthFailures = 0;
    return;
  }
  consecutiveAuthFailures += 1;
  if (consecutiveAuthFailures < AUTH_FAILURE_THRESHOLD) return;
  const expected = expectedAuthMode();
  let loginUrl = data.login_url || fallbackLoginUrl();
  if (expected === "shop") loginUrl = "/employees/shops/login/";
  else if (expected === "employee") loginUrl = "/employees/login/";
  redirectToLogin(loginUrl);
}

function installFetchGuard() {
  if (typeof window.fetch !== "function" || window.fetch.__myshopSessionGuard) {
    return;
  }
  const originalFetch = window.fetch.bind(window);
  const wrapped = async (...args) => {
    const response = await originalFetch(...withActivityStamp(args));
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
  installActivityTracker();
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
