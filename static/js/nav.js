/* ==========================================================================
   nav.js — the sidebar, on both viewports.
   --------------------------------------------------------------------------
   One button, two behaviours, because the sidebar is two different things:

     < 992px  an off-canvas drawer over the content   -> #app-shell.nav-open
     >= 992px a permanent column that collapses to an
              icon rail, so the content gets the room -> <html data-nav="rail">

   The desktop state is written to <html> rather than to #app-shell so that it
   can be applied in <head>, before the first paint — the same reason theme.js
   sets the palette there. Setting it on #app-shell would mean waiting for the
   element to exist and letting the full-width sidebar flash first.

   Kept per device in localStorage, like the appearance settings: which machine
   you are at decides how much screen you have, not which account you use.
   ========================================================================== */
(function (global) {
  "use strict";

  const KEY = "cms.nav";
  const RAIL = "rail";
  const DESKTOP = "(min-width: 992px)";

  function stored() {
    try { return localStorage.getItem(KEY); } catch (e) { return null; }
  }

  function save(state) {
    try {
      if (state === RAIL) { localStorage.setItem(KEY, RAIL); }
      else { localStorage.removeItem(KEY); }
    } catch (e) { /* private mode — the session still works, it just forgets */ }
  }

  function isRail() {
    return document.documentElement.dataset.nav === RAIL;
  }

  function isDesktop() {
    return !global.matchMedia || global.matchMedia(DESKTOP).matches;
  }

  /* ---------------------------------------------------------- tooltips --- */
  /* In the rail there is nothing but an icon, so the label has to come back on
     hover. Bootstrap's tooltip is used rather than a CSS one because the
     sidebar scrolls, and a scroll container clips its own children. */
  let tips = [];

  function disposeTips() {
    tips.forEach(function (tip) { tip.dispose(); });
    tips = [];
  }

  function buildTips() {
    disposeTips();
    if (!global.bootstrap || !global.bootstrap.Tooltip) { return; }
    document.querySelectorAll(".app-sidebar .nav-link").forEach(function (link) {
      const label = link.querySelector(".nav-label");
      if (!label) { return; }
      tips.push(new global.bootstrap.Tooltip(link, {
        title: label.textContent.trim(),
        placement: document.documentElement.dir === "rtl" ? "left" : "right",
        trigger: "hover focus",
        container: "body"
      }));
    });
  }

  function syncTips() {
    if (isDesktop() && isRail()) { buildTips(); } else { disposeTips(); }
  }

  /* ------------------------------------------------------------- state --- */
  function applyDesktop(state) {
    const root = document.documentElement;
    if (state === RAIL) { root.dataset.nav = RAIL; }
    else { delete root.dataset.nav; }
  }

  function syncButtons() {
    const expanded = isDesktop() ? !isRail()
      : document.getElementById("app-shell")?.classList.contains("nav-open") === true;
    document.querySelectorAll("[data-nav-toggle]").forEach(function (button) {
      button.setAttribute("aria-expanded", String(expanded));
    });
  }

  function toggle() {
    const shell = document.getElementById("app-shell");
    if (isDesktop()) {
      const next = isRail() ? "" : RAIL;
      applyDesktop(next);
      save(next);
      syncTips();
    } else {
      shell?.classList.toggle("nav-open");
    }
    syncButtons();
    return isRail();
  }

  function close() {
    document.getElementById("app-shell")?.classList.remove("nav-open");
    syncButtons();
  }

  /* Runs in <head>: the attribute is on <html>, which already exists. */
  applyDesktop(stored());

  document.addEventListener("DOMContentLoaded", function () {
    syncButtons();
    syncTips();

    document.addEventListener("click", function (event) {
      if (event.target.closest("[data-nav-toggle]")) { toggle(); }
      else if (event.target.closest("[data-nav-close]")) { close(); }
    });

    /* Crossing the breakpoint swaps which behaviour the button has, and the
       drawer must not stay latched open on a desktop layout. */
    if (global.matchMedia) {
      global.matchMedia(DESKTOP).addEventListener("change", function () {
        close();
        syncTips();
        syncButtons();
      });
    }
  });

  global.nav = { toggle, close, isRail };
})(window);
