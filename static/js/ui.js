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
    renderRows, countTo, withBusy, pager
  };
})(window);
