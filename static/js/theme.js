/* ==========================================================================
   theme.js — palette, mode and density.
   --------------------------------------------------------------------------
   Two layers of preference:
     1. the center default, set by an admin in Settings and rendered into
        <html data-theme data-bs-theme data-density>;
     2. a per-user override kept in localStorage on that machine.

   Nothing here knows about components: it only writes the --brand-* ramp and
   three data attributes, which is all app.css / themes.css / buttons.css read.
   ========================================================================== */
(function (global) {
  "use strict";

  const KEY = "cms.appearance";
  const PRESETS = ["teal", "indigo", "violet", "emerald", "sunset", "slate"];
  const RAMP_STOPS = [
    [50, 0.94], [100, 0.82], [200, 0.62], [300, 0.36], [400, 0.15],
    [500, 0.00], [600, -0.16], [700, -0.32], [800, -0.47], [900, -0.62]
  ];

  /* ------------------------------------------------------------- colour -- */
  function hexToRgb(hex) {
    const value = hex.replace("#", "").trim();
    const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
    return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  }

  function rgbToHex(rgb) {
    return "#" + rgb.map((c) => Math.max(0, Math.min(255, Math.round(c)))
      .toString(16).padStart(2, "0")).join("");
  }

  /** Mix towards white (amount > 0) or black (amount < 0). */
  function shade(rgb, amount) {
    const target = amount >= 0 ? 255 : 0;
    const ratio = Math.abs(amount);
    return rgb.map((c) => c + (target - c) * ratio);
  }

  /** Build the ten-stop --brand ramp from a single hex. */
  function ramp(hex) {
    const base = hexToRgb(hex);
    const out = {};
    RAMP_STOPS.forEach(function ([stop, amount]) {
      out[stop] = rgbToHex(shade(base, amount));
    });
    return out;
  }

  /** WCAG-ish relative luminance, used to keep text readable on the accent. */
  function luminance(hex) {
    const [r, g, b] = hexToRgb(hex).map(function (c) {
      const channel = c / 255;
      return channel <= 0.03928 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }

  function isValidHex(value) {
    return typeof value === "string" && /^#?[0-9a-fA-F]{6}$/.test(value.trim());
  }

  /* ------------------------------------------------------------- storage -- */
  function stored() {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; }
  }

  function save(patch) {
    const next = Object.assign(stored(), patch);
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch (e) { /* private mode */ }
    return next;
  }

  function clear() {
    try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ }
  }

  /* --------------------------------------------------------------- apply -- */
  function defaults() {
    const root = document.documentElement;
    return {
      theme: root.dataset.defaultTheme || "teal",
      accent: root.dataset.defaultAccent || "#1f6f8b",
      mode: root.dataset.defaultMode || "light",
      density: root.dataset.defaultDensity || "comfortable"
    };
  }

  function effective() {
    return Object.assign(defaults(), stored());
  }

  function resolveMode(mode) {
    if (mode !== "auto") { return mode; }
    const dark = global.matchMedia && global.matchMedia("(prefers-color-scheme: dark)").matches;
    return dark ? "dark" : "light";
  }

  function apply(settings) {
    const root = document.documentElement;
    const state = settings || effective();

    root.dataset.theme = state.theme;
    root.dataset.density = state.density;
    root.setAttribute("data-bs-theme", resolveMode(state.mode));

    // A custom accent writes the ramp inline; presets come from themes.css.
    if (state.theme === "custom" && isValidHex(state.accent)) {
      const hex = state.accent.startsWith("#") ? state.accent : "#" + state.accent;
      const stops = ramp(hex);
      Object.entries(stops).forEach(function ([stop, colour]) {
        root.style.setProperty("--brand-" + stop, colour);
      });
      root.style.setProperty("--bs-primary", stops[500]);
      root.style.setProperty("--bs-link-color", stops[600]);
      root.style.setProperty("--bs-link-hover-color", stops[700]);
    } else {
      RAMP_STOPS.forEach(function ([stop]) { root.style.removeProperty("--brand-" + stop); });
      ["--bs-primary", "--bs-link-color", "--bs-link-hover-color"].forEach(function (name) {
        root.style.removeProperty(name);
      });
    }
    return state;
  }

  function set(patch) {
    const state = apply(Object.assign(effective(), patch));
    save(patch);
    document.dispatchEvent(new CustomEvent("appearance:change", { detail: state }));
    return state;
  }

  function reset() {
    clear();
    const state = apply(defaults());
    document.dispatchEvent(new CustomEvent("appearance:change", { detail: state }));
    return state;
  }

  /* Follow the OS when the user picked "auto". */
  if (global.matchMedia) {
    global.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () {
      if (effective().mode === "auto") { apply(); }
    });
  }

  global.theme = {
    PRESETS, apply, set, reset, effective, defaults, ramp, isValidHex, luminance
  };

  apply();  // runs in <head>, before first paint — no flash of the wrong palette
})(window);
