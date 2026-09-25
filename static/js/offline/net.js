/**
 * Shared reachability helper for non-module scripts (catalog panels).
 */

export async function isAppOnline() {
  try {
    const { isOnline } = await import("./connectivity.js");
    return isOnline();
  } catch (_err) {
    return typeof navigator === "undefined" || navigator.onLine;
  }
}
