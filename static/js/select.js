/* ==========================================================================
   select.js — every <select> in the app, upgraded in place.

   Why not a library: this needs Arabic-aware search (a receptionist typing
   "احمد" must find "أحمد"), RTL, the project's own tokens, and it must not add
   a megabyte to a screen that runs on a cheap PC in a tutoring centre.

   The native <select> stays in the DOM and stays the source of truth: pages
   keep reading `element.value`, keep listening for `change`, and a form post
   still carries the field. This file only draws a nicer control over it, so a
   page that never heard of it keeps working — including one where JS fails and
   the browser falls back to the native control.
   ========================================================================== */
(function (global) {
  "use strict";

  /* Below this many options a search box is noise, not help. */
  const SEARCH_THRESHOLD = 8;
  const upgraded = new WeakSet();
  /* Panels are moved out to <body> (or to the enclosing modal) so no ancestor's
     overflow can clip them — which also means nothing removes them when their
     control goes away with a closed modal. Hence the registry and the sweep. */
  const panels = [];

  function sweep() {
    for (let i = panels.length - 1; i >= 0; i -= 1) {
      if (!panels[i].select.isConnected) {
        panels[i].panel.remove();
        panels.splice(i, 1);
      }
    }
  }

  /* ------------------------------------------------------------- search -- */

  const TASHKEEL = /[ً-ْٰـ]/g;

  /**
   * Fold the differences an Egyptian typist does not think about: hamza on the
   * alef, taa marbuta vs haa, alef maqsura vs yaa, and any tashkeel that was
   * pasted in from somewhere else. "احمد على" then matches "أحمد علي".
   */
  function normalize(text) {
    return String(text || "")
      .toLowerCase()
      .replace(TASHKEEL, "")
      .replace(/[أإآٱ]/g, "ا")   // أ إ آ ٱ -> ا
      .replace(/ة/g, "ه")                        // ة -> ه
      .replace(/ى/g, "ي")                        // ى -> ي
      .replace(/ؤ/g, "و")                        // ؤ -> و
      .replace(/ئ/g, "ي")                        // ئ -> ي
      .replace(/\s+/g, " ")
      .trim();
  }

  /* -------------------------------------------------------------- build -- */

  function upgrade(select) {
    if (
      !select ||
      upgraded.has(select) ||
      select.multiple ||
      select.dataset.noEnhance !== undefined ||
      select.closest("[data-no-enhance]")
    ) {
      return;
    }
    upgraded.add(select);

    const wrap = document.createElement("div");
    wrap.className = "xselect";
    if (select.classList.contains("form-select-sm")) { wrap.classList.add("xselect-sm"); }
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(select);
    select.classList.add("xselect-native");

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "xselect-trigger";
    trigger.setAttribute("role", "combobox");
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.innerHTML =
      '<span class="xselect-label"></span>' +
      '<svg class="icon xselect-caret" aria-hidden="true"><use href="#i-chevron"></use></svg>';
    wrap.appendChild(trigger);

    const panel = document.createElement("div");
    panel.className = "xselect-panel";
    panel.innerHTML =
      '<div class="xselect-search">' +
      '<svg class="icon" aria-hidden="true"><use href="#i-search"></use></svg>' +
      '<input type="text" class="xselect-input" autocomplete="off" spellcheck="false">' +
      "</div>" +
      '<ul class="xselect-list" role="listbox" tabindex="-1"></ul>';
    /* Inside a modal the panel has to live inside that modal: Bootstrap traps
       focus within it and would yank focus straight back out of a search box
       parked on <body>. Position is fixed either way, so nothing clips it. */
    (select.closest(".modal") || document.body).appendChild(panel);

    const search = panel.querySelector(".xselect-input");
    const list = panel.querySelector(".xselect-list");
    let options = [];
    let active = -1;
    let open = false;

    /* ----------------------------------------------------------- render -- */

    function readOptions() {
      // `hidden` is how the dependent selects (stage -> grade) narrow their
      // choices, so it has to mean hidden here too.
      options = Array.from(select.options).filter(function (option) {
        return !option.hidden && !option.closest("optgroup[hidden]");
      });
    }

    function labelFor(option) {
      return option ? (option.textContent || "").trim() : "";
    }

    function paintTrigger() {
      const current = select.selectedIndex >= 0 ? select.options[select.selectedIndex] : null;
      const label = trigger.querySelector(".xselect-label");
      const text = labelFor(current);
      // An empty first option is a placeholder ("الكل", "—"): show it greyed.
      const placeholder = !current || current.value === "";
      label.textContent = text || "—";
      label.classList.toggle("is-placeholder", placeholder);
      trigger.classList.toggle("is-invalid", select.classList.contains("is-invalid"));
      trigger.disabled = select.disabled;
      trigger.setAttribute("aria-label", select.getAttribute("aria-label") || labelText() || text);
    }

    function labelText() {
      const label = select.id ? document.querySelector('label[for="' + select.id + '"]') : null;
      return label ? label.textContent.trim() : "";
    }

    function paintList() {
      const query = normalize(search.value);
      const matches = options.filter(function (option) {
        return !query || normalize(option.textContent).includes(query);
      });

      list.innerHTML = matches.length
        ? matches
            .map(function (option) {
              const selected = option.selected;
              return (
                '<li class="xselect-option' + (selected ? " is-selected" : "") +
                (option.disabled ? " is-disabled" : "") + '"' +
                ' role="option" aria-selected="' + (selected ? "true" : "false") + '"' +
                ' data-index="' + option.index + '">' +
                '<span class="xselect-option-text"></span>' +
                (selected ? '<svg class="icon" aria-hidden="true"><use href="#i-check"></use></svg>' : "") +
                "</li>"
              );
            })
            .join("")
        : '<li class="xselect-empty">' + gettext("لا توجد نتائج") + "</li>";

      // textContent, not innerHTML: option labels come from the database.
      Array.from(list.querySelectorAll(".xselect-option")).forEach(function (node, index) {
        node.querySelector(".xselect-option-text").textContent = labelFor(matches[index]);
      });

      active = matches.findIndex(function (option) { return option.selected; });
      if (active < 0 && matches.length) { active = 0; }
      paintActive();
    }

    function paintActive() {
      const nodes = list.querySelectorAll(".xselect-option");
      nodes.forEach(function (node, index) {
        node.classList.toggle("is-active", index === active);
      });
      const current = nodes[active];
      if (current) { current.scrollIntoView({ block: "nearest" }); }
    }

    function refresh() {
      readOptions();
      paintTrigger();
      if (open) { paintList(); }
    }

    /* ------------------------------------------------------ open / close -- */

    function place() {
      const box = trigger.getBoundingClientRect();
      const below = window.innerHeight - box.bottom;
      const height = Math.min(320, Math.max(below, box.top) - 16);
      const flip = below < 200 && box.top > below;

      // Fixed, not absolute: the control is often inside a card or a modal
      // with its own overflow, and an absolute panel would be clipped by it.
      panel.style.position = "fixed";
      panel.style.width = box.width + "px";
      panel.style.left = box.left + "px";
      panel.style.maxHeight = height + "px";
      if (flip) {
        panel.style.top = "auto";
        panel.style.bottom = (window.innerHeight - box.top + 4) + "px";
      } else {
        panel.style.bottom = "auto";
        panel.style.top = (box.bottom + 4) + "px";
      }
    }

    function show() {
      if (open || select.disabled) { return; }
      open = true;
      readOptions();
      search.value = "";
      panel.classList.add("show");
      // Search only where it earns its place.
      const wanted = select.dataset.search;
      const searchable = wanted === "always" || (wanted !== "never" && options.length >= SEARCH_THRESHOLD);
      panel.classList.toggle("no-search", !searchable);
      place();
      paintList();
      trigger.setAttribute("aria-expanded", "true");
      wrap.classList.add("is-open");
      if (searchable) { search.focus(); } else { list.focus(); }
      document.addEventListener("click", onDocumentClick, true);
      window.addEventListener("resize", place);
      window.addEventListener("scroll", place, true);
    }

    function hide() {
      if (!open) { return; }
      open = false;
      panel.classList.remove("show");
      trigger.setAttribute("aria-expanded", "false");
      wrap.classList.remove("is-open");
      document.removeEventListener("click", onDocumentClick, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    }

    function onDocumentClick(event) {
      if (!panel.contains(event.target) && !wrap.contains(event.target)) { hide(); }
    }

    function choose(index) {
      const option = select.options[index];
      if (!option || option.disabled) { return; }
      select.selectedIndex = index;
      paintTrigger();
      hide();
      trigger.focus();
      // The page listens for `change` on the native element — that contract is
      // the whole reason the native element is still here.
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    /* ---------------------------------------------------------- events --- */

    trigger.addEventListener("click", function () {
      if (open) { hide(); } else { show(); }
    });

    trigger.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        show();
      } else if (event.key.length === 1) {
        // Type-ahead on a short list: reveal the box so the typist can see
        // what they typed, instead of filtering behind their back.
        show();
        panel.classList.remove("no-search");
        search.value = event.key;
        search.focus();
        paintList();
      }
    });

    search.addEventListener("input", paintList);

    panel.addEventListener("keydown", function (event) {
      const nodes = list.querySelectorAll(".xselect-option");
      if (event.key === "ArrowDown") {
        event.preventDefault();
        active = Math.min(nodes.length - 1, active + 1);
        paintActive();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        active = Math.max(0, active - 1);
        paintActive();
      } else if (event.key === "Home") {
        event.preventDefault();
        active = 0;
        paintActive();
      } else if (event.key === "End") {
        event.preventDefault();
        active = nodes.length - 1;
        paintActive();
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (nodes[active]) { choose(Number(nodes[active].dataset.index)); }
      } else if (event.key === "Escape" || event.key === "Tab") {
        hide();
        if (event.key === "Escape") { trigger.focus(); }
      }
    });

    list.addEventListener("click", function (event) {
      const node = event.target.closest(".xselect-option");
      if (node && !node.classList.contains("is-disabled")) {
        choose(Number(node.dataset.index));
      }
    });

    list.addEventListener("mousemove", function (event) {
      const node = event.target.closest(".xselect-option");
      if (!node) { return; }
      active = Array.from(list.querySelectorAll(".xselect-option")).indexOf(node);
      paintActive();
    });

    /* Clicking the field's <label> should open this, not focus a hidden node. */
    if (select.id) {
      const label = document.querySelector('label[for="' + select.id + '"]');
      if (label) {
        label.addEventListener("click", function (event) {
          event.preventDefault();
          show();
        });
      }
    }

    /* Options are rewritten all the time here: dependent selects, modals that
       fill a list after a fetch, `option.hidden` filtering. */
    new MutationObserver(refresh).observe(select, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["hidden", "disabled", "class", "value", "selected"],
    });

    select.addEventListener("change", paintTrigger);

    /* `element.value = "x"` fires no event, and several screens do exactly
       that to reset a filter. Delegate to the real setter, then repaint. */
    const native = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
    Object.defineProperty(select, "value", {
      configurable: true,
      get: function () { return native.get.call(this); },
      set: function (value) { native.set.call(this, value); paintTrigger(); },
    });

    panels.push({ select: select, panel: panel });

    refresh();
  }

  /** Upgrade every select under `root` (default: the document). */
  function enhance(root) {
    sweep();
    (root || document).querySelectorAll("select").forEach(upgrade);
  }

  document.addEventListener("DOMContentLoaded", function () { enhance(); });
  /* A closed modal takes its fields with it. */
  document.addEventListener("hidden.bs.modal", sweep);

  global.xselect = { enhance, upgrade, normalize };
})(window);
