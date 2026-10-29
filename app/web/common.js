// Shared helpers for every GroundedBI page.
// Text from the server is always inserted with textContent or createElement,
// never parsed as HTML (prevents cross-site scripting).
"use strict";

(function () {
  const MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  const REQUEST_TIMEOUT_MS = 15000;

  // Build DOM nodes without HTML parsing.
  function h(tag, props, children) {
    const el = document.createElement(tag);
    if (props) {
      for (const [key, value] of Object.entries(props)) {
        if (value === undefined || value === null || value === false) continue;
        if (key === "className") el.className = value;
        else if (key === "text") el.textContent = value;
        else if (key === "dataset") Object.assign(el.dataset, value);
        else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
        else el.setAttribute(key, value === true ? "" : String(value));
      }
    }
    for (const child of [].concat(children || [])) {
      if (child === null || child === undefined || child === false) continue;
      el.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return el;
  }

  // Same, for SVG elements.
  function s(tag, attrs, children) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") el.textContent = value;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
      else el.setAttribute(key, String(value));
    }
    for (const child of [].concat(children || [])) {
      if (child) el.append(child);
    }
    return el;
  }

  function monthLabel(ym, short) {
    const match = /^(\d{4})-(\d{2})$/.exec(ym || "");
    if (!match) return ym || "";
    const name = MONTHS[Number(match[2]) - 1] || match[2];
    return (short ? name.slice(0, 3) : name) + " " + match[1];
  }

  function quarterLabel(start, end) {
    const a = /^(\d{4})-(\d{2})$/.exec(start);
    const b = /^(\d{4})-(\d{2})$/.exec(end);
    if (!a || !b || a[1] !== b[1]) return null;
    const m1 = Number(a[2]);
    const m2 = Number(b[2]);
    if (m2 - m1 === 2 && (m1 - 1) % 3 === 0) return "Q" + ((m1 - 1) / 3 + 1) + " " + a[1];
    return null;
  }

  function periodLabel(period, short) {
    if (!period) return "";
    const start = period.start_month || period.start;
    const end = period.end_month || period.end;
    if (!start) return "";
    if (!end || start === end) return monthLabel(start, short);
    const quarter = quarterLabel(start, end);
    if (quarter) return quarter;
    return monthLabel(start, true) + " to " + monthLabel(end, true);
  }

  function listLabel(items) {
    if (items.length <= 1) return items.join("");
    return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
  }

  // Whole dollars, rounded half up, from integer cents. Matches the API's display strings.
  function formatCents(cents) {
    const negative = cents < 0;
    const dollars = Math.floor((Math.abs(cents) + 50) / 100);
    const text = "$" + dollars.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return negative && dollars !== 0 ? "-" + text : text;
  }

  // Short axis labels: $0, $500K, $1.5M.
  function compactDollars(cents) {
    const dollars = cents / 100;
    if (dollars >= 1e6) return "$" + (Math.round(dollars / 1e5) / 10).toString() + "M";
    if (dollars >= 1e3) return "$" + Math.round(dollars / 1e3).toString() + "K";
    return "$" + Math.round(dollars).toString();
  }

  async function getJSON(url, options) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort("timeout"), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, Object.assign({ signal: controller.signal }, options));
      let body = null;
      try {
        body = await response.json();
      } catch (_) {
        body = null;
      }
      return { ok: response.ok, status: response.status, body, requestId: response.headers.get("x-request-id") };
    } finally {
      clearTimeout(timer);
    }
  }

  // The one place that knows the /analytics/meta response shape:
  // { first_date: "YYYY-MM-DD" | null, last_date, products: [], regions: [], categories: [] }
  function adaptMeta(body) {
    if (!body) return null;
    const list = (value) => (Array.isArray(value) ? value : []);
    return {
      first: body.first_date ? String(body.first_date).slice(0, 7) : null,
      last: body.last_date ? String(body.last_date).slice(0, 7) : null,
      products: list(body.products),
      regions: list(body.regions),
      categories: list(body.categories),
    };
  }

  async function loadMeta() {
    try {
      const res = await getJSON("/analytics/meta");
      const meta = res.ok ? adaptMeta(res.body) : null;
      return meta && meta.first && meta.last ? meta : null;
    } catch (_) {
      return null;
    }
  }

  // "Synthetic sales data for an electronics retailer, January 2026 to June 2026, covering ..."
  function renderScope(el, meta) {
    if (!el) return;
    if (!meta) {
      el.textContent = "Synthetic sales data for an electronics retailer.";
      return;
    }
    const parts = [];
    if (meta.products.length) parts.push(meta.products.length + " products");
    if (meta.regions.length) parts.push(meta.regions.length + " regions");
    if (meta.categories.length) parts.push(meta.categories.length + " categories");
    el.replaceChildren(
      "Synthetic sales data for an electronics retailer, ",
      h("strong", { text: monthLabel(meta.first) + " to " + monthLabel(meta.last) }),
      parts.length ? ", covering " + listLabel(parts) + "." : "."
    );
  }

  window.GB = Object.freeze({
    MONTHS,
    h,
    s,
    monthLabel,
    quarterLabel,
    periodLabel,
    listLabel,
    formatCents,
    compactDollars,
    getJSON,
    adaptMeta,
    loadMeta,
    renderScope,
  });
})();
