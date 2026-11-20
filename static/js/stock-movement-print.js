/**
 * Prints stock movement receipts.
 *
 * Two triggers share one fetch+print path:
 *
 * 1. Redirect marker — stock pages redirect back with `?print=<kind>:<ids>`
 *    (plus optional `pfrom`/`pto` transfer labels). On load this script swaps
 *    the marker out of the URL, fetches the receipt payload from the
 *    movement-receipt endpoint, and hands it to RichcomPrinter (configured
 *    printer or browser fallback).
 * 2. Print buttons — `[data-movement-print]` elements carrying
 *    `data-print-kind` + `data-print-ids` (movement ledger rows) fetch and
 *    print on click.
 *
 * `data-receipt-url` on #movement-print-root points at the endpoint.
 */
(function () {
  "use strict";

  const root = document.getElementById("movement-print-root");
  const receiptUrl = (root?.dataset?.receiptUrl || "").trim();

  const printPayload = (data) => {
    if (!data?.receipt_text || !window.RichcomPrinter?.printReceipt) return null;
    return window.RichcomPrinter.printReceipt({
      text: data.receipt_text,
      channel: data.print_via || "",
      qr: data.receipt_qr || null,
      fontStyle: data.receipt_font || null,
      ticket: data.receipt_ticket || null,
      paperWidth: data.receipt_paper_width || "",
    });
  };

  const fetchAndPrint = (kind, ids, extra = {}) => {
    if (!receiptUrl || !kind || !ids) return Promise.resolve();
    const url = new URL(receiptUrl, window.location.origin);
    url.searchParams.set("kind", kind);
    url.searchParams.set("ids", ids);
    for (const [key, value] of Object.entries(extra)) {
      if (value) url.searchParams.set(key, value);
    }
    return fetch(url.toString(), {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then((response) =>
        response.ok ? response.json().catch(() => null) : null
      )
      .then((data) => {
        if (data?.ok) return printPayload(data);
        return null;
      })
      .catch(() => {
        /* printing is best-effort; the movement already saved */
      });
  };

  // Delegated print buttons (movement ledger rows etc.).
  document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-movement-print]");
    if (!button) return;
    event.preventDefault();
    const kind = (button.dataset.printKind || "").trim().toLowerCase();
    const ids = (button.dataset.printIds || "").trim();
    if (!kind || !ids || button.disabled) return;
    button.disabled = true;
    fetchAndPrint(kind, ids).finally(() => {
      button.disabled = false;
    });
  });

  if (!receiptUrl) return;

  // Redirect marker: print the receipt for the just-recorded movement(s).
  const params = new URLSearchParams(window.location.search);
  const marker = (params.get("print") || "").trim();
  if (!marker) return;

  const sep = marker.indexOf(":");
  const kind = sep === -1 ? "" : marker.slice(0, sep).trim().toLowerCase();
  const ids = sep === -1 ? "" : marker.slice(sep + 1).trim();
  if (!kind || !ids) return;

  const pfrom = (params.get("pfrom") || "").trim();
  const pto = (params.get("pto") || "").trim();

  // Strip the marker so refresh/back does not print twice.
  params.delete("print");
  params.delete("pfrom");
  params.delete("pto");
  const cleanSearch = params.toString();
  try {
    window.history.replaceState(
      null,
      "",
      window.location.pathname +
        (cleanSearch ? `?${cleanSearch}` : "") +
        window.location.hash
    );
  } catch (_err) {
    /* history API unavailable — nothing to clean */
  }

  fetchAndPrint(kind, ids, { pfrom, pto });
})();
