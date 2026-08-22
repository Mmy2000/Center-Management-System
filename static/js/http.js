/* CSRF-aware fetch wrapper for the JSON envelope (docs/05 §G.2). */
(function (global) {
  "use strict";

  function getCookie(name) {
    const match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return match ? decodeURIComponent(match[2]) : null;
  }

  async function request(method, url, body, options) {
    options = options || {};
    const headers = Object.assign(
      { "X-Requested-With": "XMLHttpRequest" },
      options.headers || {}
    );

    if (method !== "GET" && method !== "HEAD") {
      headers["X-CSRFToken"] = getCookie("csrftoken") || "";
      if (body !== undefined && body !== null && !(body instanceof FormData)) {
        headers["Content-Type"] = "application/json";
      }
    }

    const init = {
      method: method,
      headers: headers,
      credentials: "same-origin",
      signal: options.signal
    };
    if (body !== undefined && body !== null) {
      init.body = body instanceof FormData ? body : JSON.stringify(body);
    }

    const response = await fetch(url, init);
    const contentType = response.headers.get("Content-Type") || "";

    if (!contentType.includes("application/json")) {
      if (!response.ok) {
        throw { ok: false, code: "ERR_HTTP_" + response.status, message: response.statusText };
      }
      return { ok: true, code: "OK", data: await response.text() };
    }

    const payload = await response.json();
    payload.status = response.status;
    if (!response.ok || payload.ok === false) {
      throw payload;
    }
    return payload;
  }

  const http = {
    getCookie: getCookie,
    get: (url, options) => request("GET", url, null, options),
    post: (url, body, options) => request("POST", url, body, options),
    patch: (url, body, options) => request("PATCH", url, body, options),
    del: (url, body, options) => request("DELETE", url, body, options),
    /* Fetch a server-rendered HTML fragment. */
    html: async function (url) {
      const response = await fetch(url, {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin"
      });
      if (!response.ok) {
        throw { ok: false, code: "ERR_HTTP_" + response.status, message: response.statusText };
      }
      return response.text();
    }
  };

  global.http = http;
})(window);
