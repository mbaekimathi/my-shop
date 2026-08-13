/**
 * Staff 6-digit ID verify with offline cache + reconnect fallback.
 */

import * as store from "./store.js";

export async function verifyStaffLoginCode({ url, code, csrfToken } = {}) {
  const normalized = String(code || "").trim();
  if (!/^\d{6}$/.test(normalized)) {
    return { ok: false, error: "Staff ID must be exactly 6 digits." };
  }
  if (!url) {
    return {
      ok: false,
      error: "Verification is unavailable. Refresh and try again.",
    };
  }

  const fromCache = async () => {
    const cached = await store.getCachedStaffVerify(normalized);
    if (!cached?.ok) return null;
    const employeeId = cached.employee_id || normalized;
    const name = cached.name || "staff";
    return {
      ok: true,
      offline: true,
      cached: true,
      employee_id: employeeId,
      name,
      message: `Offline (saved): ${name} (${employeeId}).`,
    };
  };

  const offlineFallback = async () => {
    const cached = await fromCache();
    if (cached) return cached;
    return {
      ok: true,
      offline: true,
      cached: false,
      employee_id: normalized,
      name: "",
      message: "Offline — staff ID will be confirmed when you reconnect.",
    };
  };

  if (typeof navigator !== "undefined" && !navigator.onLine) {
    return offlineFallback();
  }

  try {
    const body = new URLSearchParams({ login_code: normalized });
    const response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "X-CSRFToken": csrfToken || "",
      },
      credentials: "same-origin",
      body,
    });
    const contentType = response.headers.get("content-type") || "";
    const data = contentType.includes("application/json")
      ? await response.json().catch(() => ({}))
      : {};
    if (!response.ok || !data.ok) {
      let error = data.error;
      if (!error) {
        if (response.status === 403 || response.status === 401) {
          error = "Shop session expired. Refresh and sign in again.";
        } else {
          error = "Not a valid active staff ID.";
        }
      }
      return { ok: false, error };
    }
    const employeeId = data.employee_id || normalized;
    const name = data.name || "staff";
    try {
      await store.cacheStaffVerify(normalized, {
        ok: true,
        employee_id: employeeId,
        name,
      });
    } catch (_cacheErr) {
      /* cache optional */
    }
    return {
      ok: true,
      offline: false,
      cached: false,
      employee_id: employeeId,
      name,
    };
  } catch (_err) {
    return offlineFallback();
  }
}
