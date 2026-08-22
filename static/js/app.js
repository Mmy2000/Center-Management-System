/* Shared behaviour: toasts, modal, keyboard shortcuts, mobile nav, downloads. */
(function (global) {
  "use strict";

  const TOAST_ICON = { success: "check", danger: "error", warning: "warn", secondary: "info", info: "info" };

  function toast(message, variant) {
    const container = document.getElementById("toast-container");
    if (!container) { return; }
    const tone = variant || "secondary";
    const el = document.createElement("div");
    el.className = "toast align-items-center text-bg-" + tone + " border-0";
    el.setAttribute("role", "alert");
    el.innerHTML =
      '<div class="d-flex"><div class="toast-body">' +
      ui.icon(TOAST_ICON[tone] || "info") +
      "<span></span></div>" +
      '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
    el.querySelector(".toast-body span").textContent = message;
    container.appendChild(el);
    const instance = new bootstrap.Toast(el, { delay: 4000 });
    instance.show();
    el.addEventListener("hidden.bs.toast", () => el.remove());
  }

  function handleError(error) {
    const message = (error && error.message) || gettext("حدث خطأ غير متوقع، برجاء المحاولة مرة أخرى");
    toast(message, "danger");
    if (error && error.field_errors) {
      Object.entries(error.field_errors).forEach(([field, messages]) => {
        const input = document.querySelector('[name="' + field + '"]');
        const slot = document.querySelector('[data-error-for="' + field + '"]');
        if (slot) { slot.textContent = messages.join(" "); }
        if (input) {
          input.classList.add("is-invalid");
          input.addEventListener("input", () => input.classList.remove("is-invalid"), { once: true });
        }
      });
    }
  }

  function clearFieldErrors(root) {
    (root || document).querySelectorAll("[data-error-for]").forEach((n) => (n.textContent = ""));
    (root || document).querySelectorAll(".is-invalid").forEach((n) => n.classList.remove("is-invalid"));
  }

  function openModal(title, bodyHtml, footerHtml) {
    document.getElementById("app-modal-title").textContent = title || "";
    document.getElementById("app-modal-body").innerHTML = bodyHtml || "";
    document.getElementById("app-modal-footer").innerHTML = footerHtml || "";
    const modal = bootstrap.Modal.getOrCreateInstance(document.getElementById("app-modal"));
    modal.show();
    const firstField = document.querySelector("#app-modal-body input, #app-modal-body select, #app-modal-body textarea");
    if (firstField) { setTimeout(() => firstField.focus(), 220); }
    return modal;
  }

  function closeModal() {
    const instance = bootstrap.Modal.getInstance(document.getElementById("app-modal"));
    if (instance) { instance.hide(); }
  }

  /** Require a typed reason before a destructive/financial action can proceed. */
  function requireReason(modalRoot, buttonId) {
    const reason = document.querySelector(modalRoot + ' [name="reason"]');
    const button = document.getElementById(buttonId);
    if (!reason || !button) { return null; }
    button.disabled = true;
    reason.addEventListener("input", () => (button.disabled = !reason.value.trim()));
    return { reason, button, value: () => reason.value.trim() };
  }

  function debounce(fn, wait) {
    let timer = null;
    return function () {
      const args = arguments;
      clearTimeout(timer);
      timer = setTimeout(() => fn.apply(this, args), wait);
    };
  }

  /** Download a server-generated file (PDF/CSV/XLSX) without leaving the page. */
  function download(url) {
    const frame = document.createElement("a");
    frame.href = url;
    frame.rel = "noopener";
    document.body.appendChild(frame);
    frame.click();
    frame.remove();
  }

  /* --------------------------------------------------------- shortcuts --- */
  document.addEventListener("keydown", function (event) {
    const tag = (event.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || tag === "select";
    if (typing) { return; }
    if (event.key === "/") {
      const search = document.getElementById("global-search");
      if (search) { event.preventDefault(); search.focus(); }
    }
    if (event.key === "s" && document.getElementById("shortcut-scan")) {
      window.location = document.getElementById("shortcut-scan").href;
    }
    if (event.key === "n" && document.getElementById("shortcut-new")) {
      window.location = document.getElementById("shortcut-new").href;
    }
  });

  /* -------------------------------------------------------- mobile nav --- */
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-nav-toggle]")) {
      document.getElementById("app-shell")?.classList.toggle("nav-open");
    }
    if (event.target.closest("[data-nav-close]")) {
      document.getElementById("app-shell")?.classList.remove("nav-open");
    }
  });

  global.app = {
    toast, handleError, clearFieldErrors, openModal, closeModal,
    requireReason, debounce, download
  };
})(window);
