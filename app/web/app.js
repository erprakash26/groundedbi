// GroundedBI web UI.
// Rules this file follows on purpose:
// - Every piece of text from the server or the user is inserted with textContent
//   or createElement, never parsed as HTML (prevents cross-site scripting).
// - The page never computes or reformats a number. It only displays the display
//   strings the API returned, so what you see is exactly what was verified.
"use strict";

(function () {
  const API = {
    ask: "/ask",
    examples: "/ask/examples",
    meta: "/analytics/meta",
  };
  const DEFAULT_QUESTION = "Why did revenue drop in March compared with February?";

  const TOOL_STEPS = {
    get_data_overview: "Looked up what data is available",
    get_change_drivers: "Calculated what drove the change",
    get_revenue_breakdown: "Broke revenue down",
    get_revenue_by_month: "Added up revenue by month",
    compare_periods: "Compared two periods",
    get_top_products: "Ranked products by revenue",
  };

  const DIMENSIONS = {
    product: "product",
    region: "region",
    category: "category",
    product_region: "product and region",
  };

  const { h, monthLabel, periodLabel, listLabel, getJSON } = window.GB;

  let inFlight = null;

  function adaptExamples(body) {
    const items = Array.isArray(body) ? body : body && Array.isArray(body.examples) ? body.examples : [];
    return items
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item.question === "string") return item.question;
        if (item && item.value && typeof item.value.question === "string") return item.value.question;
        return null;
      })
      .filter(Boolean)
      .slice(0, 6);
  }

  // ---------- page setup ----------

  const els = {};

  function init() {
    els.form = document.getElementById("ask-form");
    els.input = document.getElementById("question");
    els.button = document.getElementById("ask-button");
    els.hint = document.getElementById("question-hint");
    els.result = document.getElementById("result");
    els.scope = document.getElementById("scope");
    els.examples = document.getElementById("examples");
    els.examplesBlock = document.getElementById("examples-block");
    els.loadingTpl = document.getElementById("tpl-loading");

    els.form.addEventListener("submit", (event) => {
      event.preventDefault();
      submitQuestion(els.input.value);
    });
    els.input.addEventListener("input", () => setHint(null));

    loadMeta();
    loadExamples();

    const fromUrl = new URLSearchParams(window.location.search).get("q");
    const first = (fromUrl || DEFAULT_QUESTION).slice(0, 500);
    els.input.value = first;
    submitQuestion(first, { replaceUrl: !fromUrl });
  }

  async function loadMeta() {
    window.GB.renderScope(els.scope, await window.GB.loadMeta());
  }

  async function loadExamples() {
    try {
      const res = await getJSON(API.examples);
      const questions = res.ok ? adaptExamples(res.body) : [];
      if (!questions.length) return;
      els.examples.replaceChildren(
        ...questions.map((q) =>
          h("li", null, h("button", { type: "button", className: "example", text: q, onclick: () => pick(q) }))
        )
      );
      els.examplesBlock.hidden = false;
    } catch (_) {
      /* examples are optional */
    }
  }

  function pick(question) {
    els.input.value = question;
    submitQuestion(question);
  }

  function setHint(message) {
    els.hint.textContent = message || "Up to 500 characters. Press Enter to ask.";
    els.hint.classList.toggle("hint-error", Boolean(message));
  }

  function setBusy(busy) {
    els.button.disabled = busy;
    els.result.setAttribute("aria-busy", busy ? "true" : "false");
  }

  async function submitQuestion(raw, opts) {
    const question = String(raw || "").trim();
    if (!question) {
      setHint("Type a question first.");
      els.input.focus();
      return;
    }
    if (question.length > 500) {
      setHint("Keep the question under 500 characters.");
      return;
    }
    setHint(null);
    updateUrl(question, opts && opts.replaceUrl);

    if (inFlight) inFlight.cancelled = true;
    const ticket = { cancelled: false };
    inFlight = ticket;

    setBusy(true);
    els.result.replaceChildren(els.loadingTpl.content.cloneNode(true));

    let res;
    try {
      res = await getJSON(API.ask, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ question }),
      });
    } catch (err) {
      if (ticket.cancelled) return;
      setBusy(false);
      const timedOut = String(err && (err.message || err)).includes("timeout") || (err && err.name === "AbortError");
      renderState("error", {
        heading: timedOut ? "The answer took too long" : "Couldn't reach the server",
        body: timedOut
          ? "The server didn't answer within 15 seconds. Try again in a moment."
          : "Check your connection and try again.",
      });
      return;
    }
    if (ticket.cancelled) return;
    setBusy(false);
    render(question, res);
  }

  function updateUrl(question, replace) {
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("q", question);
      if (replace) url.searchParams.delete("q");
      window.history.replaceState(null, "", url);
    } catch (_) {
      /* not important */
    }
  }

  // ---------- rendering by status ----------

  function render(question, res) {
    const body = res.body || {};

    if (!res.ok) {
      const error = body.error || {};
      const requestId = error.request_id || res.requestId;
      if (res.status === 422) {
        renderState("neutral", {
          heading: "That question can't be sent as written",
          body: "Questions need to be between 1 and 500 characters of plain text.",
          requestId,
        });
      } else if (res.status === 503) {
        renderState("failed", {
          heading: "The data isn't available right now",
          body: error.message || "The database couldn't be opened.",
          requestId,
        });
      } else {
        renderState("failed", {
          heading: "Something went wrong on the server",
          body: "The error was logged. Try again, or mention the request ID if you report it.",
          requestId,
        });
      }
      return;
    }

    switch (body.status) {
      case "answered":
        renderAnswer(question, body);
        break;
      case "clarification_needed":
        renderClarify(body);
        break;
      case "out_of_scope":
        renderState("neutral", {
          heading: "That's outside what this data can answer",
          body: body.answer || body.message || "",
          requestId: body.request_id,
          trace: body,
        });
        break;
      case "grounding_failed":
        renderState("failed", {
          heading: "No verified answer",
          body:
            body.message ||
            "An answer was produced, but not every number in it could be traced to a query result, so it was withheld.",
          requestId: body.request_id,
          trace: body,
        });
        break;
      case "limit_exceeded":
        renderState("neutral", {
          heading: "This question needed too many steps",
          body: body.message || "Try asking about one period or one comparison at a time.",
          requestId: body.request_id,
          trace: body,
        });
        break;
      default:
        renderState("failed", {
          heading: "Something went wrong",
          body: body.message || "The server returned an unexpected result.",
          requestId: body.request_id,
        });
    }
  }

  function renderState(kind, opts) {
    const box = h("div", { className: "state state-" + kind }, [
      h("h2", { text: opts.heading }),
      opts.body ? h("p", { className: "state-body", text: opts.body }) : null,
      opts.extra || null,
      opts.requestId ? h("p", { className: "request-id", text: "Request ID: " + opts.requestId }) : null,
    ]);
    const nodes = [box];
    if (opts.trace && Array.isArray(opts.trace.trace) && opts.trace.trace.length) {
      nodes.push(renderTrace(opts.trace));
    }
    els.result.replaceChildren(...nodes);
  }

  function renderClarify(body) {
    const text = body.clarifying_question || body.message || "";
    // Example questions offered after "For example:" become buttons.
    const parts = text.split(/For example:/i);
    const suggestions = parts.length > 1 ? Array.from(parts[1].matchAll(/"([^"]{6,160})"/g), (m) => m[1]) : [];
    const lead = parts[0].trim();
    const extra = suggestions.length
      ? h("div", { className: "suggestions" }, [
          h("p", { className: "suggestions-label", text: "You could ask:" }),
          h(
            "ul",
            { className: "examples-list" },
            suggestions.map((q) =>
              h("li", null, h("button", { type: "button", className: "example", text: q, onclick: () => pick(q) }))
            )
          ),
        ])
      : null;
    renderState("clarify", {
      heading: "One detail needed",
      body: lead,
      extra,
      requestId: body.request_id,
    });
  }

  // ---------- the answer: memo + ledger + trace ----------

  function renderAnswer(question, body) {
    const evidence = Array.isArray(body.evidence) ? body.evidence : [];
    const grounded = Boolean(body.grounding && body.grounding.passed);
    const tokens = grounded ? verifiedTokens(evidence) : [];

    const answerEl = h("p", { className: "memo-answer" });
    let tickCount = 0;
    for (const piece of splitByTokens(body.answer || "", tokens)) {
      if (piece.token) {
        const mark = h("mark", {
          className: "verified",
          tabindex: "0",
          title: "Checked against the query result",
          dataset: { value: piece.text },
          text: piece.text,
        });
        mark.style.setProperty("--i", String(tickCount++));
        answerEl.append(mark);
      } else {
        answerEl.append(document.createTextNode(piece.text));
      }
    }

    const footer = h("div", { className: "memo-footer" }, [
      grounded
        ? h("span", { className: "stamp" }, [
            h("span", { className: "stamp-icon", "aria-hidden": "true" }),
            "Verified: " +
              body.grounding.numbers_checked +
              (body.grounding.numbers_checked === 1 ? " number" : " numbers") +
              " checked against query results",
          ])
        : null,
      typeof body.duration_ms === "number" ? h("span", { text: "Answered in " + formatMs(body.duration_ms) }) : null,
    ]);

    const memo = h("article", { className: "memo is-fresh", "aria-label": "Answer" }, [
      h("p", { className: "memo-question" }, ["You asked ", h("q", { text: question })]),
      answerEl,
      coverageNote(evidence),
      footer,
    ]);

    const ledgers = evidence.map(renderLedger).filter(Boolean);
    const evidenceCol = h("section", { className: "evidence", "aria-label": "Evidence" }, [
      h("h2", { className: "panel-heading" }, [
        "Evidence ",
        h("span", { className: "muted", text: "from the database" }),
      ]),
      ...(ledgers.length ? ledgers : [h("p", { className: "ledger-caption", text: "No table for this answer." })]),
    ]);

    const grid = h("div", { className: "result-grid" }, [
      h("div", null, [h("h2", { className: "panel-heading", text: "Answer" }), memo]),
      evidenceCol,
    ]);

    els.result.replaceChildren(grid, renderTrace(body));
    linkMarksToCells(els.result);
    setTimeout(() => memo.classList.remove("is-fresh"), 4000);
  }

  function formatMs(ms) {
    return (Math.round(ms * 10) / 10).toString() + " ms";
  }

  // Strings the answer is allowed to quote: display strings and percentages from the evidence.
  function verifiedTokens(evidence) {
    const set = new Set();
    const visit = (value, key) => {
      if (value && typeof value === "object") {
        for (const [k, v] of Object.entries(value)) visit(v, k);
        return;
      }
      if (typeof value !== "string" || !key) return;
      if (key.endsWith("_display")) set.add(value);
      if (key === "pct_change" || key === "pct_change_abs" || key === "share_of_total_change") set.add(value + "%");
    };
    for (const item of evidence) visit(item && item.data, null);
    return Array.from(set).filter((t) => /\d/.test(t)).sort((a, b) => b.length - a.length);
  }

  function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function splitByTokens(text, tokens) {
    if (!tokens.length || !text) return [{ text }];
    const pattern = new RegExp("(" + tokens.map(escapeRegExp).join("|") + ")", "g");
    const pieces = [];
    let last = 0;
    for (const match of text.matchAll(pattern)) {
      const start = match.index;
      const end = start + match[0].length;
      const before = text[start - 1] || "";
      const after = text.slice(end, end + 2);
      // Don't tick a fragment of a longer number, e.g. "$1,2" inside "$1,234".
      const glued = /[\d$]/.test(before) || /^\d|^[.,]\d/.test(after);
      if (glued) continue;
      if (start > last) pieces.push({ text: text.slice(last, start) });
      pieces.push({ text: match[0], token: true });
      last = end;
    }
    if (last < text.length) pieces.push({ text: text.slice(last) });
    return pieces;
  }

  function coverageNote(evidence) {
    const missing = [];
    let coverage = null;
    for (const item of evidence) {
      const data = (item && item.data) || {};
      coverage = coverage || data.data_coverage;
      for (const prefix of ["", "period_a_", "period_b_"]) {
        const covered = data[prefix + "period_fully_covered"];
        const months = data[prefix + "uncovered_months"];
        if (covered === false && Array.isArray(months)) {
          for (const m of months) if (!missing.includes(m)) missing.push(m);
        }
      }
    }
    if (!missing.length) return null;
    const shown = missing.slice(0, 6).map((m) => monthLabel(m));
    const more = missing.length > 6 ? " and " + (missing.length - 6) + " more months" : "";
    const range =
      coverage && coverage.first_month && coverage.last_month
        ? " The data covers " + monthLabel(coverage.first_month) + " to " + monthLabel(coverage.last_month) + "."
        : "";
    return h("p", {
      className: "coverage-note",
      text: "No data for " + listLabel(shown) + more + "." + range,
    });
  }

  // ---------- ledgers ----------

  function renderLedger(item) {
    if (!item || !item.data) return null;
    const data = item.data;
    const tool = item.tool || "";

    if (tool === "compare_periods" || (!Array.isArray(data.rows) && data.revenue_a_display)) {
      return ledgerBlock(captionFor(tool, data), ["Period", "Revenue"], [
        [periodLabel(data.period_a, true), data.revenue_a_display],
        [periodLabel(data.period_b, true), data.revenue_b_display],
        {
          total: true,
          cells: [
            "Change",
            data.change_display + (data.pct_change ? " (" + data.pct_change + "%)" : ""),
          ],
        },
      ]);
    }

    if (!Array.isArray(data.rows) || !data.rows.length) return null;

    const first = data.rows[0];
    const labelKey = "label" in first ? "label" : "month" in first ? "month" : null;
    const valueKeys = Object.keys(first).filter((k) => k.endsWith("_display") && !k.endsWith("_abs_display"));
    const pctKeys = Object.keys(first).filter((k) => k === "share_of_total_change" || k === "pct_change");

    const headers = [labelHeader(data, labelKey, tool)]
      .concat(valueKeys.map((k) => columnHeader(k, data)))
      .concat(pctKeys.map((k) => (k === "share_of_total_change" ? "Share of change" : "Change %")));

    const rows = data.rows.map((row) => [
      labelKey === "month" ? monthLabel(row.month) : row[labelKey],
      ...valueKeys.map((k) => row[k]),
      ...pctKeys.map((k) => (row[k] == null ? "" : row[k] + "%")),
    ]);

    if (data.total_revenue_a_display && data.total_revenue_b_display && valueKeys.includes("revenue_a_display")) {
      const totalCells = ["Total"];
      for (const k of valueKeys) {
        if (k === "revenue_a_display") totalCells.push(data.total_revenue_a_display);
        else if (k === "revenue_b_display") totalCells.push(data.total_revenue_b_display);
        else if (k === "change_display") totalCells.push(data.total_change_display || "");
        else totalCells.push("");
      }
      for (let i = 0; i < pctKeys.length; i++) totalCells.push("");
      rows.push({ total: true, cells: totalCells });
    }

    const note =
      typeof data.driver_count === "number" && data.driver_count > data.rows.length
        ? "Largest " + data.rows.length + " of " + data.driver_count + " " + plural(DIMENSIONS[data.dimension] || "items") + " by size of change."
        : null;

    return ledgerBlock(captionFor(tool, data), headers, rows, note);
  }

  function plural(word) {
    if (word === "product and region") return "product and region pairs";
    if (word.endsWith("y")) return word.slice(0, -1) + "ies";
    return word.endsWith("s") ? word : word + "s";
  }

  function labelHeader(data, labelKey, tool) {
    if (labelKey === "month") return "Month";
    if (tool === "get_top_products") return "Product";
    const dim = DIMENSIONS[data.dimension];
    if (dim) return dim.charAt(0).toUpperCase() + dim.slice(1);
    return "Item";
  }

  function columnHeader(key, data) {
    if (key === "revenue_a_display") return periodLabel(data.period_a, true) || "Before";
    if (key === "revenue_b_display") return periodLabel(data.period_b, true) || "After";
    if (key === "change_display") return "Change";
    if (key === "revenue_display") return "Revenue";
    const base = key.replace(/_display$/, "").replace(/_/g, " ");
    return base.charAt(0).toUpperCase() + base.slice(1);
  }

  function captionFor(tool, data) {
    const dim = DIMENSIONS[data.dimension];
    const period = periodLabel(data.period || data, false);
    switch (tool) {
      case "get_change_drivers":
        return "Change by " + (dim || "item") + ", " + periodLabel(data.period_a) + " to " + periodLabel(data.period_b);
      case "compare_periods":
        return "Revenue in " + periodLabel(data.period_a) + " and " + periodLabel(data.period_b);
      case "get_revenue_breakdown":
        return "Revenue by " + (dim || "item") + (period ? ", " + period : "");
      case "get_revenue_by_month":
        return "Revenue by month" + (period ? ", " + period : "");
      case "get_top_products":
        return "Top products by revenue" + (period ? ", " + period : "");
      default:
        return "Query result";
    }
  }

  function ledgerBlock(caption, headers, rows, note) {
    const thead = h("thead", null, h("tr", null, headers.map((text) => h("th", { scope: "col", text }))));
    const tbody = h(
      "tbody",
      null,
      rows.map((row) => {
        const isTotal = !Array.isArray(row) && row.total;
        const cells = Array.isArray(row) ? row : row.cells;
        return h(
          "tr",
          { className: isTotal ? "total" : null },
          cells.map((value, i) => {
            const text = value == null ? "" : String(value);
            if (i === 0) return h("th", { scope: "row", text });
            return h("td", {
              className: text.startsWith("-") ? "neg" : null,
              dataset: { value: text },
              text,
            });
          })
        );
      })
    );
    return h("div", { className: "ledger-block" }, [
      h("p", { className: "ledger-caption", text: caption }),
      h("div", { className: "ledger-scroll", tabindex: "0", role: "region", "aria-label": caption }, [
        h("table", { className: "ledger" }, [thead, tbody]),
      ]),
      note ? h("p", { className: "ledger-note", text: note }) : null,
    ]);
  }

  // Hover or focus a ticked number to see which ledger cell it came from.
  function linkMarksToCells(root) {
    const cells = Array.from(root.querySelectorAll(".ledger td"));
    for (const mark of root.querySelectorAll(".verified")) {
      const value = mark.dataset.value;
      const matches = cells.filter((td) => td.dataset.value && td.dataset.value.includes(value));
      const on = () => {
        mark.classList.add("is-linked");
        matches.forEach((td) => td.classList.add("is-linked"));
      };
      const off = () => {
        mark.classList.remove("is-linked");
        matches.forEach((td) => td.classList.remove("is-linked"));
      };
      mark.addEventListener("mouseenter", on);
      mark.addEventListener("mouseleave", off);
      mark.addEventListener("focus", on);
      mark.addEventListener("blur", off);
    }
  }

  // ---------- trace ----------

  function renderTrace(body) {
    const steps = (body.trace || []).map((step) =>
      h("li", { className: "trace-step" + (step.ok === false ? " is-error" : "") }, [
        h("p", { className: "trace-title" }, [
          TOOL_STEPS[step.tool] || step.tool,
          typeof step.duration_ms === "number" ? h("span", { className: "duration", text: formatMs(step.duration_ms) }) : null,
        ]),
        h("p", { className: "trace-args" }, [
          h("span", { className: "arg" }, [h("b", { text: "tool " }), step.tool]),
          ...Object.entries(step.arguments || {}).map(([k, v]) =>
            h("span", { className: "arg" }, [h("b", { text: k.replace(/_/g, " ") + " " }), String(v)])
          ),
        ]),
        step.ok === false ? h("p", { className: "trace-meta", text: "Failed: " + (step.error_code || "error") }) : null,
      ])
    );

    if (body.grounding) {
      steps.push(
        h("li", { className: "trace-step" + (body.grounding.passed ? "" : " is-error") }, [
          h("p", {
            className: "trace-title",
            text: body.grounding.passed
              ? "Checked every number in the answer against the query results"
              : "Numbers in the answer didn't match the query results, so it was withheld",
          }),
          h("p", {
            className: "trace-meta",
            text: body.grounding.numbers_checked + " numbers checked.",
          }),
        ])
      );
    }

    const total = typeof body.duration_ms === "number" ? ", " + formatMs(body.duration_ms) : "";
    return h("details", { className: "trace" }, [
      h("summary", null, [
        "How this answer was produced ",
        h("span", { className: "muted", text: "(" + steps.length + (steps.length === 1 ? " step" : " steps") + total + ")" }),
      ]),
      h("ol", { className: "trace-steps" }, steps),
      body.request_id
        ? h("p", { className: "request-id", text: "Request ID " + body.request_id + ". Every log line for this question carries it." })
        : null,
    ]);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
