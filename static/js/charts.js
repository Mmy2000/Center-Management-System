/* Small SVG charts, drawn by hand (TASK-122).

   No charting library, for the same reason `core/ratelimit.py` has no rate-limit
   library: what these charts need is a few hundred lines, and a 200KB vendored
   dependency to draw thirty rectangles would cost every console page load for
   the rest of the project's life.

   Colour never appears in this file. Every mark carries a class and
   `console.css` supplies the hue, so the charts follow the operator's chosen
   theme and light/dark mode for free — the same trick the rest of the design
   system uses. The three status hues were validated for colour-vision
   separation and contrast against both surfaces; see console.css.

   Two forms only:
     charts.columns(el, points, opts)  stacked columns — the hero chart
     charts.spark(values)              a sparkline, as an SVG string for a cell
*/
(function (global) {
  "use strict";

  const SVG = "http://www.w3.org/2000/svg";

  /* Mark specs, fixed across both charts. */
  const BAR_MAX = 24;      // never let a column fill its slot; the air is the spacer
  const GAP = 2;           // surface gap between stacked segments and between bars
  const RADIUS = 4;        // rounded data-end, square at the baseline
  const AXIS_BAND = 22;    // reserved for x labels, so they are never clipped
  const Y_GUTTER = 44;     // reserved for y ticks
  const TOP_PAD = 10;      // headroom so the tallest column is not flush to the edge

  //: The stack, bottom to top. Bottom is the one that is almost always there.
  const SERIES = [
    { key: "ok", cls: "c-ok" },
    { key: "c4", cls: "c-warn" },
    { key: "c5", cls: "c-bad" }
  ];

  function el(name, attrs) {
    const node = document.createElementNS(SVG, name);
    for (const key in attrs) {
      if (attrs[key] !== null && attrs[key] !== undefined) {
        node.setAttribute(key, attrs[key]);
      }
    }
    return node;
  }

  function fmt(value) {
    return Number(value || 0).toLocaleString("en-EG");
  }

  function clock(epochSeconds) {
    const d = new Date(epochSeconds * 1000);
    return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  }

  /** Round a maximum up to something a person would choose for an axis.

      The ladder is deliberately fine. A coarse one (1 / 2 / 5 / 10) rounds a
      peak of 270 up to 500 and leaves the tallest column filling half the
      plot, which reads as "quiet" for traffic that is anything but. */
  const NICE_STEPS = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];

  function niceMax(value) {
    if (value <= 5) { return 5; }
    const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
    const scaled = value / magnitude;
    const step = NICE_STEPS.find(function (s) { return scaled <= s; }) || 10;
    return Math.round(step * magnitude);
  }

  /** A rect whose top corners are rounded and whose baseline stays square. */
  function topRoundedPath(x, y, w, h, r) {
    const radius = Math.max(0, Math.min(r, w / 2, h));
    return (
      "M" + x + "," + (y + h) +
      "V" + (y + radius) +
      "a" + radius + "," + radius + " 0 0 1 " + radius + "," + -radius +
      "h" + (w - radius * 2) +
      "a" + radius + "," + radius + " 0 0 1 " + radius + "," + radius +
      "V" + (y + h) + "Z"
    );
  }

  /* ------------------------------------------------------------ sparkline */

  /** A 30-point shape for a table cell, returned as an SVG string.

      Single series, so no legend: the column header says what it is. The row's
      own numbers are the value; this only answers "is that number rising?",
      which a number on its own cannot. */
  function spark(values, options) {
    options = options || {};
    const width = options.width || 84;
    const height = options.height || 24;
    const points = (values || []).map(Number);

    if (!points.length || points.every(function (v) { return !v; })) {
      return (
        '<svg class="spark is-flat" width="' + width + '" height="' + height +
        '" viewBox="0 0 ' + width + " " + height + '" aria-hidden="true">' +
        '<line x1="0" y1="' + (height - 1) + '" x2="' + width + '" y2="' + (height - 1) + '"/>' +
        "</svg>"
      );
    }

    const peak = Math.max.apply(null, points) || 1;
    const step = points.length > 1 ? width / (points.length - 1) : width;
    // 1.5px stroke sits inside the box, so the plot stops short of both edges.
    const top = 2;
    const bottom = height - 2;

    const coords = points.map(function (value, index) {
      const x = index * step;
      const y = bottom - (value / peak) * (bottom - top);
      return [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
    });

    const line = coords
      .map(function (p, i) { return (i ? "L" : "M") + p[0] + "," + p[1]; })
      .join("");
    const fill = line + "L" + coords[coords.length - 1][0] + "," + height +
                 "L" + coords[0][0] + "," + height + "Z";

    const last = coords[coords.length - 1];
    return (
      '<svg class="spark" width="' + width + '" height="' + height +
      '" viewBox="0 0 ' + width + " " + height + '" aria-hidden="true">' +
      '<path class="spark-fill" d="' + fill + '"/>' +
      '<path class="spark-line" d="' + line + '"/>' +
      '<circle class="spark-dot" cx="' + last[0] + '" cy="' + last[1] + '" r="2.5"/>' +
      "</svg>"
    );
  }

  /* ------------------------------------------------------- stacked columns */

  /** Requests per minute, stacked 2xx / 4xx / 5xx.

      Columns rather than an area: these are discrete one-minute buckets, and an
      area would draw a slope between them that the data does not contain. */
  function columns(target, points, options) {
    options = options || {};
    const host = typeof target === "string" ? document.getElementById(target) : target;
    if (!host) { return; }

    host.innerHTML = "";
    points = points || [];

    const labels = options.labels || { ok: "2xx", c4: "4xx", c5: "5xx" };
    const height = options.height || 190;
    const width = Math.max(240, host.clientWidth || host.parentNode.clientWidth || 640);

    const totals = points.map(function (p) {
      return (p.ok || 0) + (p.c4 || 0) + (p.c5 || 0);
    });
    const peak = Math.max.apply(null, totals.concat([0]));

    if (!points.length || !peak) {
      host.appendChild(emptyNote(options.emptyMessage));
      return;
    }

    const top = niceMax(peak);
    const plotTop = TOP_PAD;
    const plotBottom = height - AXIS_BAND;
    const plotHeight = plotBottom - plotTop;
    const plotLeft = Y_GUTTER;
    const plotWidth = width - Y_GUTTER - 8;
    const slot = plotWidth / points.length;
    const barWidth = Math.max(2, Math.min(BAR_MAX, slot - GAP));

    const svg = el("svg", {
      class: "chart",
      width: width,
      height: height,
      viewBox: "0 0 " + width + " " + height,
      role: "img",
      "aria-label": options.ariaLabel || ""
    });

    const scale = function (value) { return (value / top) * plotHeight; };

    /* --- gridlines and y ticks: solid hairlines, one step off the surface --- */
    [0, 0.5, 1].forEach(function (fraction) {
      const value = top * fraction;
      const y = Math.round(plotBottom - scale(value)) + 0.5;
      svg.appendChild(el("line", {
        class: "chart-grid", x1: plotLeft, y1: y, x2: plotLeft + plotWidth, y2: y
      }));
      const tick = el("text", {
        class: "chart-tick", x: plotLeft - 8, y: y + 4, "text-anchor": "end"
      });
      tick.textContent = fmt(Math.round(value));
      svg.appendChild(tick);
    });

    /* ------------------------------------------------------------- columns */
    const bars = el("g", {});
    points.forEach(function (point, index) {
      const x = plotLeft + index * slot + (slot - barWidth) / 2;
      const stack = SERIES.map(function (s) {
        return { cls: s.cls, key: s.key, value: point[s.key] || 0 };
      }).filter(function (s) { return s.value > 0; });

      // `cursor` walks the stack's true boundaries; the 2px gap is taken out
      // of each segment's *drawn* height, never out of the boundary — so the
      // whole column still measures the total value however many gaps it has.
      let cursor = plotBottom;
      stack.forEach(function (segment, position) {
        const isTop = position === stack.length - 1;
        const span = scale(segment.value);
        const y = cursor - span;
        const h = Math.max(1, span - (position ? GAP : 0));
        const node = isTop
          ? el("path", { class: "chart-bar " + segment.cls, d: topRoundedPath(x, y, barWidth, h, RADIUS) })
          : el("rect", { class: "chart-bar " + segment.cls, x: x, y: y, width: barWidth, height: h });
        bars.appendChild(node);
        cursor = y;
      });
    });
    svg.appendChild(bars);

    /* ---------------------------------------------------------- x labels --
       Six at most, and never every column: thirty timestamps on one axis is a
       smear. The rest are reachable on hover and in the table view. */
    const every = Math.max(1, Math.ceil(points.length / 6));
    points.forEach(function (point, index) {
      if (index % every && index !== points.length - 1) { return; }
      const label = el("text", {
        class: "chart-tick",
        x: plotLeft + index * slot + slot / 2,
        y: height - 6,
        "text-anchor": "middle"
      });
      label.textContent = clock(point.minute);
      svg.appendChild(label);
    });

    /* ------------------------------------------------------- the hit layer --
       The mark is the target on a column chart, but the painted mark is only a
       few pixels wide. Each column gets a full-height transparent rect across
       its whole slot, so the pointer only has to be near. */
    const hits = el("g", { class: "chart-hits" });
    points.forEach(function (point, index) {
      const hit = el("rect", {
        class: "chart-hit",
        x: plotLeft + index * slot,
        y: plotTop,
        width: slot,
        height: plotHeight,
        tabindex: "0",
        role: "button",
        "aria-label": readout(point, labels, true)
      });
      const show = function () { showTip(point); hit.classList.add("is-on"); };
      const hide = function () { hideTip(); hit.classList.remove("is-on"); };
      hit.addEventListener("pointerenter", show);
      hit.addEventListener("focus", show);
      hit.addEventListener("pointerleave", hide);
      hit.addEventListener("blur", hide);
      hits.appendChild(hit);
    });
    svg.appendChild(hits);

    host.appendChild(svg);

    /* -------------------------------------------------------------- tooltip */
    const tip = document.createElement("div");
    tip.className = "chart-tip";
    tip.hidden = true;
    host.appendChild(tip);

    function showTip(point) {
      tip.innerHTML = "";
      const when = document.createElement("div");
      when.className = "chart-tip-when";
      when.textContent = clock(point.minute);
      tip.appendChild(when);

      SERIES.forEach(function (s) {
        const row = document.createElement("div");
        row.className = "chart-tip-row";
        const key = document.createElement("span");
        key.className = "chart-tip-key " + s.cls;
        const value = document.createElement("strong");
        value.textContent = fmt(point[s.key] || 0);
        const name = document.createElement("span");
        name.className = "chart-tip-name";
        // Untrusted only in principle here, but textContent is the habit.
        name.textContent = labels[s.key];
        row.appendChild(key);
        row.appendChild(value);
        row.appendChild(name);
        tip.appendChild(row);
      });
      tip.hidden = false;
    }

    function hideTip() { tip.hidden = true; }

    host.addEventListener("pointermove", function (event) {
      if (tip.hidden) { return; }
      const box = host.getBoundingClientRect();
      const x = event.clientX - box.left;
      // Clamped to the card on both sides, so the tip never spills out of it
      // near either edge. Physical `left` on purpose: the plot is pinned LTR.
      tip.style.left = Math.min(Math.max(8, x + 14), box.width - tip.offsetWidth - 8) + "px";
    });
  }

  function readout(point, labels, includeTime) {
    const parts = SERIES.map(function (s) {
      return labels[s.key] + " " + fmt(point[s.key] || 0);
    });
    return (includeTime ? clock(point.minute) + " — " : "") + parts.join("، ");
  }

  function emptyNote(message) {
    const box = document.createElement("div");
    box.className = "chart-empty";
    box.textContent = message || "";
    return box;
  }

  global.charts = { columns: columns, spark: spark, SERIES: SERIES };
})(window);
