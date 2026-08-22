/* ==========================================================================
   ui.js — the shared rendering layer.
   Every screen draws its tables, badges, skeletons and empty states through
   these helpers, so a change to how a table looks happens in exactly one file.
   ========================================================================== */
(function (global) {
  "use strict";

  const ESCAPE = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

  /** Escape anything that came from the database before it reaches innerHTML. */
  function esc(value) {
    if (value === null || value === undefined) { return ""; }
    return String(value).replace(/[&<>"']/g, (ch) => ESCAPE[ch]);
  }

  function icon(name, extraClass) {
    return '<svg class="icon ' + (extraClass || "") + '" aria-hidden="true"><use href="#i-' + name + '"></use></svg>';
  }

  /** Soft badge. tone: success | danger | warning | info | primary | secondary | dark */
  function badge(text, tone) {
    return '<span class="badge badge-soft badge-soft-' + (tone || "secondary") + '">' + esc(text) + "</span>";
  }

  function money(value, currency) {
    const number = Number(value || 0);
    const text = number.toLocaleString("en-EG", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return '<span class="num">' + text + (currency ? (" " + gettext("ج.م")) : "") + "</span>";
  }

  /** Accept an element or the id of one, so callers can pass either. */
  function node(target) {
    return typeof target === "string" ? document.getElementById(target) : target;
  }

  /**
   * How many columns a table body spans. Read from the header rather than made
   * a parameter: the header is already the truth, and a caller who passes the
   * wrong number gets a skeleton that does not line up with the real rows.
   */
  function columnsOf(tbody) {
    if (!tbody) { return 1; }
    // data-columns for the few tables that carry no header row.
    if (tbody.dataset && tbody.dataset.columns) { return Number(tbody.dataset.columns); }
    const table = tbody.closest("table");
    const headers = table ? table.querySelectorAll("thead th") : [];
    if (headers.length) { return headers.length; }
    const firstRow = tbody.rows[0];
    return firstRow ? firstRow.cells.length : 1;
  }

  function skeletonRows(tbody, columns, rows) {
    if (!tbody) { return; }
    const count = rows || 4;
    let html = "";
    for (let r = 0; r < count; r += 1) {
      html += "<tr>";
      for (let c = 0; c < columns; c += 1) {
        html += '<td><span class="skeleton" style="width:' + (45 + ((r * 13 + c * 29) % 45)) + '%"></span></td>';
      }
      html += "</tr>";
    }
    tbody.innerHTML = html;
  }

  function emptyRow(columns, message, iconName) {
    return (
      '<tr><td colspan="' + columns + '">' +
      '<div class="empty-state">' + icon(iconName || "empty", "icon-xl") +
      '<div class="empty-state-title">' + esc(message || gettext("لا توجد بيانات")) + "</div></div></td></tr>"
    );
  }

  function errorRow(columns, message) {
    return (
      '<tr><td colspan="' + columns + '">' +
      '<div class="empty-state text-danger">' + icon("error", "icon-xl") +
      '<div class="empty-state-title">' + esc(message || gettext("تعذر التحميل")) + "</div></div></td></tr>"
    );
  }

  /**
   * Render rows into a <tbody>.
   *   columns: [{ key, render?(row) -> html, className?, raw? }]
   * `render` returns trusted HTML; plain `key` values are escaped.
   */
  function renderRows(tbody, rows, columns, options) {
    if (!tbody) { return; }
    options = options || {};
    if (!rows || !rows.length) {
      tbody.innerHTML = emptyRow(columns.length, options.emptyMessage, options.emptyIcon);
      return;
    }
    tbody.innerHTML = rows
      .map(function (row, index) {
        const attrs = options.rowAttrs ? options.rowAttrs(row, index) : "";
        const cells = columns
          .map(function (column) {
            const html = column.render ? column.render(row, index) : esc(row[column.key]);
            const value = html === "" || html === null || html === undefined ? "—" : html;
            return "<td" + (column.className ? ' class="' + column.className + '"' : "") + ">" + value + "</td>";
          })
          .join("");
        return "<tr " + attrs + ">" + cells + "</tr>";
      })
      .join("");
  }

  /** Animate a number towards its new value — used by dashboards and counters. */
  function countTo(node, target, options) {
    if (!node) { return; }
    options = options || {};
    const reduced = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const to = Number(target);
    if (!isFinite(to) || reduced || options.instant) {
      node.textContent = options.format ? options.format(to) : target;
      return;
    }
    const from = Number(String(node.textContent).replace(/[^\d.-]/g, "")) || 0;
    if (from === to) { return; }
    const duration = options.duration || 450;
    const started = performance.now();
    function step(now) {
      const progress = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      const current = from + (to - from) * eased;
      node.textContent = options.format
        ? options.format(current)
        : Math.round(current).toLocaleString("en-EG");
      if (progress < 1) { requestAnimationFrame(step); }
    }
    requestAnimationFrame(step);
  }

  /* ---------------------------------------------------------- loading -- */

  /**
   * Fill a table body with a loading state, run the fetch, and put an error
   * row there if it fails:
   *
   *     ui.table("students-body", async function (body) {
   *       const result = await http.get(url);
   *       body.innerHTML = …;
   *     });
   *
   * Every list screen goes through this, so "loading", "empty" and "failed"
   * look the same everywhere and no screen can forget one of them.
   *
   * options.quiet skips the skeleton — for a timer that refreshes a table the
   * user is reading, where a flash of grey bars every few seconds is worse
   * than no feedback at all.
   */
  async function table(target, task, options) {
    const tbody = node(target);
    if (!tbody) { return task(); }
    const columns = columnsOf(tbody);
    if (!(options && options.quiet)) {
      // Match the rows on screen so paging does not jolt the page height.
      skeletonRows(tbody, columns, Math.min(8, Math.max(3, tbody.rows.length || 5)));
    }
    try {
      return await task(tbody, columns);
    } catch (error) {
      tbody.innerHTML = errorRow(columns, error && error.message);
      if (global.app && global.app.handleError) { global.app.handleError(error); }
    }
  }

  /**
   * The same contract for anything that is not a table — a stats card, a
   * summary panel, a chart. The container is dimmed and gets a spinner
   * through `.is-busy`; its old content stays visible underneath.
   */
  async function region(target, task) {
    const box = node(target);
    if (box) { box.classList.add("is-busy"); }
    try {
      return await task(box);
    } catch (error) {
      if (global.app && global.app.handleError) { global.app.handleError(error); }
    } finally {
      if (box) { box.classList.remove("is-busy"); }
    }
  }

  /**
   * The thin bar across the top of the window. Driven by the events http.js
   * fires, so every request on every page shows it without a line of page
   * code — including the ones that write, not just the ones that read.
   */
  const progress = (function () {
    let pending = 0;
    let bar = null;
    let width = 0;
    let timer = null;

    function element() {
      if (!bar) {
        bar = document.createElement("div");
        bar.className = "load-bar";
        bar.setAttribute("aria-hidden", "true");
        document.body.appendChild(bar);
      }
      return bar;
    }

    function paint() {
      element().style.width = width + "%";
    }

    /* Creep towards 90% while waiting: the request has no progress to report,
       and a bar that stalls at one place reads as a hang. */
    function creep() {
      timer = setTimeout(function () {
        width = Math.min(90, width + Math.max(0.4, (90 - width) / 12));
        paint();
        creep();
      }, 220);
    }

    function start() {
      pending += 1;
      if (pending > 1) { return; }
      clearTimeout(timer);
      element().classList.add("show");
      width = 12;
      paint();
      creep();
    }

    function done() {
      pending = Math.max(0, pending - 1);
      if (pending) { return; }
      clearTimeout(timer);
      width = 100;
      paint();
      setTimeout(function () {
        if (pending) { return; }          // a new request started meanwhile
        element().classList.remove("show");
        width = 0;
        paint();
      }, 240);
    }

    return { start, done };
  })();

  document.addEventListener("http:start", progress.start);
  document.addEventListener("http:end", progress.done);

  /** Put a button into a loading state and restore it when the promise settles. */
  async function withBusy(button, task) {
    if (!button) { return task(); }
    const original = button.innerHTML;
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> ' + original;
    try {
      return await task();
    } finally {
      button.disabled = false;
      button.innerHTML = original;
    }
  }

  /** Simple pagination footer wiring shared by every list screen. */
  function pager(ids, onChange) {
    const prev = document.getElementById(ids.prev);
    const next = document.getElementById(ids.next);
    const label = document.getElementById(ids.label);
    let page = 1;
    if (prev) { prev.addEventListener("click", () => { if (page > 1) { page -= 1; onChange(page); } }); }
    if (next) { next.addEventListener("click", () => { page += 1; onChange(page); }); }
    return {
      get page() { return page; },
      reset() { page = 1; },
      update(data, noun) {
        if (label) {
          label.textContent = (gettext("إجمالي") + " ") + data.count + " " + (noun || "") +
            (" " + gettext("— صفحة") + " ") + data.page + (" " + gettext("من") + " ") + data.pages;
        }
        if (prev) { prev.disabled = !data.has_previous; }
        if (next) { next.disabled = !data.has_next; }
      }
    };
  }

  global.ui = {
    esc, icon, badge, money, skeletonRows, emptyRow, errorRow,
    renderRows, countTo, withBusy, pager,
    columnsOf, table, region, progress
  };
})(window);
