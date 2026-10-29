// GroundedBI Explore page: stat tiles and charts drawn from the /analytics API.
// Totals, changes and percentages all come from the server. The page only
// formats integer cents for display and picks which month to highlight.
"use strict";

(function () {
  const { h, s, monthLabel, periodLabel, formatCents, compactDollars, getJSON } = window.GB;

  const charts = []; // { el, draw } kept so we can redraw on resize

  function query(path, params) {
    const qs = new URLSearchParams(params).toString();
    return getJSON(path + (qs ? "?" + qs : ""));
  }

  function addMonths(ym, n) {
    const [y, m] = ym.split("-").map(Number);
    const index = y * 12 + (m - 1) + n;
    return Math.floor(index / 12) + "-" + String((index % 12) + 1).padStart(2, "0");
  }

  function monthsBetween(first, last) {
    const out = [];
    for (let m = first; m <= last && out.length < 120; m = addMonths(m, 1)) out.push(m);
    return out;
  }

  function signedPct(pct) {
    if (pct === null || pct === undefined) return "n/a";
    const text = String(pct);
    return (text.startsWith("-") ? "−" + text.slice(1) : "+" + text) + "%";
  }

  function showError(el, message) {
    el.replaceChildren(h("p", { className: "chart-status", text: message }));
  }

  // ---------- tooltip ----------

  function attachTip(container, mark, title, value) {
    const show = () => {
      let tip = container.querySelector(".chart-tip");
      if (!tip) {
        tip = h("div", { className: "chart-tip", role: "presentation" });
        container.append(tip);
      }
      tip.replaceChildren(h("strong", { text: value }), title);
      const box = container.getBoundingClientRect();
      const r = mark.getBoundingClientRect();
      tip.style.left = r.left - box.left + r.width / 2 + "px";
      tip.style.top = r.top - box.top + "px";
      tip.hidden = false;
    };
    const hide = () => {
      const tip = container.querySelector(".chart-tip");
      if (tip) tip.hidden = true;
    };
    mark.addEventListener("pointerenter", show);
    mark.addEventListener("pointerleave", hide);
    mark.addEventListener("focus", show);
    mark.addEventListener("blur", hide);
  }

  function tableView(headers, rows) {
    return h("details", { className: "chart-table" }, [
      h("summary", { text: "Show as a table" }),
      h("div", { className: "ledger-scroll" }, [
        h("table", { className: "ledger" }, [
          h("thead", null, h("tr", null, headers.map((t) => h("th", { scope: "col", text: t })))),
          h(
            "tbody",
            null,
            rows.map((r) => h("tr", null, [h("th", { scope: "row", text: r[0] }), ...r.slice(1).map((v) => h("td", { text: v }))]))
          ),
        ]),
      ]),
    ]);
  }

  // ---------- chart shapes ----------

  // Rounded data-end, square at the baseline (columns grow up from the baseline).
  function columnPath(x, y, w, height) {
    const r = Math.min(4, w / 2, height);
    return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + height}Z`;
  }

  // Bars grow right from the baseline; rounded end on the right.
  function barPath(x, y, width, thick) {
    const r = Math.min(4, thick / 2, width);
    return `M${x},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + thick - r}Q${x + width},${y + thick} ${x + width - r},${y + thick}H${x}Z`;
  }

  function niceMax(value) {
    if (value <= 0) return 1;
    const exp = Math.pow(10, Math.floor(Math.log10(value)));
    for (const step of [1, 2, 2.5, 5, 10]) {
      if (value <= step * exp) return step * exp;
    }
    return 10 * exp;
  }

  // Columns: one series, months on the x axis.
  function drawColumns(container, items, focusIndex, focusNote) {
    const width = Math.max(300, container.clientWidth);
    const height = 280;
    const pad = { top: 44, right: 8, bottom: 30, left: 56 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const max = niceMax(Math.max(...items.map((d) => d.value)));
    const band = plotW / items.length;
    const barW = Math.min(36, band * 0.55);

    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": "Revenue by month, column chart" });
    for (let i = 0; i <= 4; i++) {
      const v = (max / 4) * i;
      const y = pad.top + plotH - (v / max) * plotH;
      svg.append(
        s("line", { class: "grid-line", x1: pad.left, x2: width - pad.right, y1: y, y2: y }),
        s("text", { class: "axis-text", x: pad.left - 8, y: y + 4, "text-anchor": "end", text: compactDollars(v) })
      );
    }
    items.forEach((d, i) => {
      const x = pad.left + band * i + (band - barW) / 2;
      const barH = Math.max(1, (d.value / max) * plotH);
      const y = pad.top + plotH - barH;
      const bar = s("path", {
        class: "bar" + (i === focusIndex ? " is-focus" : ""),
        d: columnPath(x, y, barW, barH),
        tabindex: 0,
        "aria-label": d.label + ": " + formatCents(d.value),
      });
      svg.append(bar);
      svg.append(s("text", { class: "axis-text", x: x + barW / 2, y: height - 8, "text-anchor": "middle", text: d.short }));
      if (i === focusIndex) {
        // On narrow charts there's only room for the short change label.
        const roomy = band >= 110;
        const note = focusNote ? (roomy ? focusNote : focusNote.split(" ")[0]) : null;
        if (roomy) {
          svg.append(s("text", { class: "value-text", x: x + barW / 2, y: y - (note ? 24 : 8), "text-anchor": "middle", text: formatCents(d.value) }));
        }
        if (note) svg.append(s("text", { class: "note-text", x: x + barW / 2, y: y - 8, "text-anchor": "middle", text: note }));
      }
      attachTip(container, bar, d.label, formatCents(d.value));
    });
    return svg;
  }

  // Horizontal bars: categories down the side, value at each bar's tip.
  function drawBars(container, items) {
    const width = Math.max(280, container.clientWidth);
    const row = 40;
    const thick = 22;
    // Fit the longest label (about 7.3px per character at 13px), within limits.
    const longest = Math.max(...items.map((d) => String(d.label).length));
    const labelW = Math.min(width * 0.45, Math.max(70, longest * 7.3 + 14));
    const valueW = 92;
    const height = items.length * row + 6;
    const max = Math.max(...items.map((d) => d.value));
    const plotW = width - labelW - valueW;

    const svg = s("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": "Bar chart" });
    svg.append(s("line", { class: "grid-line", x1: labelW, x2: labelW, y1: 0, y2: height }));
    items.forEach((d, i) => {
      const y = i * row + (row - thick) / 2;
      const barWidth = Math.max(2, (d.value / max) * plotW);
      const bar = s("path", {
        class: "bar",
        d: barPath(labelW, y, barWidth, thick),
        tabindex: 0,
        "aria-label": d.label + ": " + formatCents(d.value),
      });
      svg.append(
        s("text", { class: "label-text", x: labelW - 10, y: y + thick / 2 + 4.5, "text-anchor": "end", text: d.label }),
        bar,
        s("text", { class: "value-text", x: labelW + barWidth + 8, y: y + thick / 2 + 4.5, text: formatCents(d.value) })
      );
      attachTip(container, bar, d.label, formatCents(d.value));
    });
    return svg;
  }

  function mountChart(el, draw, headers, rows) {
    const render = () => el.replaceChildren(draw(el), tableView(headers, rows));
    render();
    charts.push({ el, render, width: el.clientWidth });
  }

  // ---------- sections ----------

  async function loadStats(meta) {
    const statsEl = document.getElementById("stats");
    const months = monthsBetween(meta.first, meta.last);
    if (months.length < 2) {
      statsEl.replaceChildren();
      return;
    }
    const half = Math.floor(months.length / 2);
    const a = { start: months[0], end: months[half - 1] };
    const b = { start: months[half], end: months[months.length - 1] };
    const res = await query("/analytics/compare", { a_start: a.start, a_end: a.end, b_start: b.start, b_end: b.end });
    if (!res.ok || !res.body) return showError(statsEl, "Couldn't load the summary.");
    const c = res.body;
    const up = c.change_cents >= 0;
    statsEl.replaceChildren(
      h("div", { className: "stat" }, [
        h("p", { className: "stat-label", text: "Revenue, " + periodLabel(a) }),
        h("p", { className: "stat-value", text: formatCents(c.revenue_a_cents) }),
        h("p", { className: "stat-note", text: monthLabel(a.start) + " to " + monthLabel(a.end) }),
      ]),
      h("div", { className: "stat" }, [
        h("p", { className: "stat-label", text: "Revenue, " + periodLabel(b) }),
        h("p", { className: "stat-value", text: formatCents(c.revenue_b_cents) }),
        h("p", { className: "stat-note", text: monthLabel(b.start) + " to " + monthLabel(b.end) }),
      ]),
      h("div", { className: "stat" }, [
        h("p", { className: "stat-label", text: "Change between the two" }),
        h("p", { className: "stat-value " + (up ? "delta-up" : "delta-down"), text: signedPct(c.pct_change) }),
        h("p", { className: "stat-note", text: (up ? "Up " : "Down ") + formatCents(Math.abs(c.change_cents)) }),
      ])
    );
  }

  async function loadMonthly(meta) {
    const el = document.getElementById("chart-monthly");
    document.getElementById("chart-monthly-sub").textContent = monthLabel(meta.first) + " to " + monthLabel(meta.last);
    const res = await query("/analytics/revenue-by-month", { start: meta.first, end: meta.last });
    const rows = res.ok && res.body && Array.isArray(res.body.rows) ? res.body.rows : null;
    if (!rows || !rows.length) return showError(el, "Couldn't load monthly revenue.");

    const items = rows.map((r) => ({
      month: r.month,
      label: monthLabel(r.month),
      short: monthLabel(r.month, true).slice(0, 3),
      value: r.revenue_cents,
    }));

    // Pick the month with the largest change from the month before (selection only).
    let focus = -1;
    let biggest = 0;
    for (let i = 1; i < items.length; i++) {
      const diff = Math.abs(items[i].value - items[i - 1].value);
      if (diff > biggest) {
        biggest = diff;
        focus = i;
      }
    }

    let note = null;
    if (focus > 0) {
      const prev = items[focus - 1].month;
      const cur = items[focus].month;
      const [cmp, drv] = await Promise.all([
        query("/analytics/compare", { a_start: prev, a_end: prev, b_start: cur, b_end: cur }),
        query("/analytics/drivers", { a_start: prev, a_end: prev, b_start: cur, b_end: cur, dimension: "product_region", limit: 1 }),
      ]);
      if (cmp.ok && cmp.body) {
        note = signedPct(cmp.body.pct_change) + " vs " + items[focus - 1].short;
        renderCallout(items[focus - 1], items[focus], cmp.body, drv.ok && drv.body && drv.body.rows ? drv.body.rows[0] : null);
      }
    }

    mountChart(
      el,
      (container) => drawColumns(container, items, focus, note),
      ["Month", "Revenue"],
      items.map((d) => [d.label, formatCents(d.value)])
    );
  }

  function renderCallout(prev, cur, cmp, topDriver) {
    const fell = cmp.change_cents < 0;
    const pct = String(cmp.pct_change || "").replace(/^-/, "");
    const sentence =
      "The biggest month-to-month change was " + cur.label + ": revenue " + (fell ? "fell " : "rose ") + pct + "% from " + prev.label + "." +
      (topDriver && topDriver.share_of_total_change
        ? " " + topDriver.label + " accounts for " + topDriver.share_of_total_change + "% of the change."
        : "");
    const question =
      "Why did revenue " + (fell ? "drop" : "rise") + " in " + cur.label.split(" ")[0] + " compared with " + prev.label.split(" ")[0] + "?";
    document.getElementById("callout").replaceChildren(
      h("div", { className: "callout" }, [
        h("p", { text: sentence }),
        h("a", { className: "button-link", href: "/?q=" + encodeURIComponent(question), text: "Ask GroundedBI why" }),
      ])
    );
  }

  async function loadBreakdown(meta, dimension, elId, subId) {
    const el = document.getElementById(elId);
    document.getElementById(subId).textContent = monthLabel(meta.first) + " to " + monthLabel(meta.last);
    const res = await query("/analytics/breakdown", { start: meta.first, end: meta.last, dimension });
    const rows = res.ok && res.body && Array.isArray(res.body.rows) ? res.body.rows : null;
    if (!rows || !rows.length) return showError(el, "Couldn't load this chart.");
    const items = rows.map((r) => ({ label: r.label, value: r.revenue_cents }));
    const head = dimension.charAt(0).toUpperCase() + dimension.slice(1);
    mountChart(el, (c) => drawBars(c, items), [head, "Revenue"], items.map((d) => [d.label, formatCents(d.value)]));
  }

  async function loadTopProducts(meta) {
    const el = document.getElementById("chart-products");
    document.getElementById("chart-products-sub").textContent = monthLabel(meta.first) + " to " + monthLabel(meta.last);
    const res = await query("/analytics/top-products", { start: meta.first, end: meta.last, limit: 5 });
    const rows = res.ok && res.body && Array.isArray(res.body.rows) ? res.body.rows : null;
    if (!rows || !rows.length) return showError(el, "Couldn't load top products.");
    const items = rows.map((r) => ({ label: r.label, value: r.revenue_cents }));
    mountChart(el, (c) => drawBars(c, items), ["Product", "Revenue"], items.map((d) => [d.label, formatCents(d.value)]));
  }

  async function init() {
    const meta = await window.GB.loadMeta();
    window.GB.renderScope(document.getElementById("scope"), meta);
    if (!meta) {
      for (const id of ["stats", "chart-monthly", "chart-region", "chart-category", "chart-products"]) {
        showError(document.getElementById(id), "The data isn't available right now.");
      }
      return;
    }
    await Promise.all([
      loadStats(meta),
      loadMonthly(meta),
      loadBreakdown(meta, "region", "chart-region", "chart-region-sub"),
      loadBreakdown(meta, "category", "chart-category", "chart-category-sub"),
      loadTopProducts(meta),
    ]);

    if ("ResizeObserver" in window) {
      const observer = new ResizeObserver(() => {
        for (const c of charts) {
          if (Math.abs(c.el.clientWidth - c.width) > 8) {
            c.width = c.el.clientWidth;
            const open = c.el.querySelector("details[open]") !== null;
            c.render();
            if (open) c.el.querySelector("details").open = true;
          }
        }
      });
      charts.forEach((c) => observer.observe(c.el));
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
