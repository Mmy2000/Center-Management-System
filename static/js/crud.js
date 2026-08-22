/* Reusable CRUD table wiring for the configuration screens (docs/05 §H.4).
   Each panel declares its endpoints and columns; this renders, creates and
   patches through the shared JSON envelope. */
(function (global) {
  "use strict";

  function el(tag, attrs, html) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => node.setAttribute(k, v));
    if (html !== undefined) { node.innerHTML = html; }
    return node;
  }

  function fieldInput(field, value) {
    const name = field.name;
    if (field.type === "select") {
      const options = (field.options || [])
        .map((o) => '<option value="' + o[0] + '"' + (String(o[0]) === String(value) ? " selected" : "") + ">" + o[1] + "</option>")
        .join("");
      return '<select class="form-select" name="' + name + '">' + options + "</select>";
    }
    if (field.type === "checkbox") {
      return '<div class="form-check"><input class="form-check-input" type="checkbox" name="' + name + '"' + (value === false ? "" : " checked") + "></div>";
    }
    const type = field.type || "text";
    return '<input class="form-control" type="' + type + '" name="' + name + '" value="' + (value === undefined || value === null ? "" : value) + '">';
  }

  function readForm(fields) {
    const payload = {};
    fields.forEach(function (field) {
      const input = document.querySelector('#app-modal [name="' + field.name + '"]');
      if (!input) { return; }
      payload[field.name] = field.type === "checkbox" ? input.checked : input.value;
    });
    return payload;
  }

  function formHtml(fields, row) {
    return fields
      .map(function (field) {
        const value = row ? row[field.name] : field.default;
        return (
          '<div class="mb-2"><label class="form-label">' + field.label + "</label>" +
          fieldInput(field, value) +
          '<div class="invalid-feedback d-block text-danger small" data-error-for="' + field.name + '"></div></div>'
        );
      })
      .join("");
  }

  function showErrors(error) {
    document.querySelectorAll("#app-modal [data-error-for]").forEach((n) => (n.textContent = ""));
    if (error && error.field_errors) {
      Object.entries(error.field_errors).forEach(function ([field, messages]) {
        const node = document.querySelector('#app-modal [data-error-for="' + field + '"]');
        if (node) { node.textContent = messages.join(" "); }
        else { app.toast(field + ": " + messages.join(" "), "danger"); }
      });
    } else {
      app.handleError(error);
    }
  }

  /* config: {mount, listUrl, detailUrl(id), fields, columns, title, canWrite, params} */
  function panel(config) {
    const mount = document.querySelector(config.mount);
    if (!mount) { return null; }
    let rows = [];

    /* The panel's chrome, drawn around whatever the table body currently is —
       skeleton rows before the fetch answers, real rows after. Painting it
       once up front is what keeps the screen from sitting empty. */
    function shell(bodyHtml) {
      const head = config.columns.map((c) => "<th>" + c.label + "</th>").join("");
      mount.innerHTML =
        '<div class="d-flex justify-content-between align-items-center mb-2">' +
        '<div class="fw-semibold">' + config.title + "</div>" +
        (config.canWrite ? ('<button class="btn btn-sm btn-primary js-new">' + gettext("إضافة") + '</button>') : "") +
        "</div>" +
        '<div class="table-responsive"><table class="table table-sm table-compact align-middle mb-0">' +
        "<thead class='table-light'><tr>" + head + "<th></th></tr></thead><tbody>" + bodyHtml + "</tbody></table></div>";
    }

    function skeleton() {
      const columns = config.columns.length + 1;
      let html = "";
      for (let r = 0; r < 3; r += 1) {
        html += "<tr>";
        for (let c = 0; c < columns; c += 1) {
          html += '<td><span class="skeleton" style="width:' + (45 + ((r * 13 + c * 29) % 45)) + '%"></span></td>';
        }
        html += "</tr>";
      }
      shell(html);
    }

    function render() {
      const body = rows.length
        ? rows.map(function (row) {
            const cells = config.columns
              .map(function (c) {
                const value = c.render ? c.render(row) : row[c.key];
                return "<td>" + (value === null || value === undefined || value === "" ? "—" : value) + "</td>";
              })
              .join("");
            return "<tr data-id='" + row.id + "'>" + cells +
              "<td class='text-end'>" + (config.canWrite ? ('<button class="btn btn-sm btn-outline-secondary js-edit">' + gettext("تعديل") + '</button>') : "") + "</td></tr>";
          }).join("")
        : ui.emptyRow(config.columns.length + 1, gettext("لا توجد بيانات"));

      shell(body);
    }

    async function load() {
      skeleton();
      try {
        const query = config.params ? "?" + new URLSearchParams(config.params).toString() : "";
        const result = await http.get(config.listUrl + query);
        rows = result.data.results;
        render();
      } catch (error) {
        shell(ui.errorRow(config.columns.length + 1, error && error.message));
        app.handleError(error);
      }
    }

    mount.addEventListener("click", function (event) {
      if (event.target.closest(".js-new")) {
        app.openModal(config.title + (" " + gettext("— إضافة")), formHtml(config.fields, null),
          ('<button class="btn btn-primary js-save">' + gettext("حفظ") + '</button>'));
        document.querySelector("#app-modal .js-save").addEventListener("click", async function () {
          try {
            await http.post(config.listUrl, readForm(config.fields));
            bootstrap.Modal.getInstance(document.getElementById("app-modal")).hide();
            app.toast(gettext("تم الحفظ"), "success");
            load();
            if (config.onChange) { config.onChange(); }
          } catch (error) { showErrors(error); }
        });
      }
      const editButton = event.target.closest(".js-edit");
      if (editButton) {
        const id = editButton.closest("tr").dataset.id;
        const row = rows.find((r) => String(r.id) === String(id));
        app.openModal(config.title + (" " + gettext("— تعديل")), formHtml(config.fields, row),
          ('<button class="btn btn-primary js-save">' + gettext("حفظ") + '</button>'));
        document.querySelector("#app-modal .js-save").addEventListener("click", async function () {
          try {
            await http.patch(config.detailUrl(id), readForm(config.fields));
            bootstrap.Modal.getInstance(document.getElementById("app-modal")).hide();
            app.toast(gettext("تم الحفظ"), "success");
            load();
            if (config.onChange) { config.onChange(); }
          } catch (error) { showErrors(error); }
        });
      }
    });

    load();
    return { reload: load, rows: () => rows };
  }

  async function options(url, labelKey) {
    const result = await http.get(url);
    return result.data.results.map((row) => [row.id, row[labelKey || "label"]]);
  }

  global.crud = { panel: panel, options: options, el: el };
})(window);
