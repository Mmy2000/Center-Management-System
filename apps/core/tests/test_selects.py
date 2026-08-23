"""The custom select (static/js/select.js) and the contract it must keep.

It draws over the native `<select>` instead of replacing it. That is the whole
design: every screen reads `element.value`, listens for `change`, and posts the
field in a form — none of which may notice that a nicer control is on top.
"""

import pathlib

from django.conf import settings

BASE = pathlib.Path(settings.BASE_DIR)
SELECT_JS = (BASE / "static" / "js" / "select.js").read_text(encoding="utf-8")
SELECT_CSS = (BASE / "static" / "css" / "select.css").read_text(encoding="utf-8")
BASE_HTML = (BASE / "templates" / "base.html").read_text(encoding="utf-8")
APP_JS = (BASE / "static" / "js" / "app.js").read_text(encoding="utf-8")
ICONS = (BASE / "templates" / "partials" / "_icons.html").read_text(encoding="utf-8")


def test_every_page_loads_it():
    assert "css/select.css" in BASE_HTML
    assert "js/select.js" in BASE_HTML


def test_the_native_element_stays_and_stays_authoritative():
    # Moved into a wrapper, never removed, and never replaced by a hidden input.
    assert "wrap.appendChild(select)" in SELECT_JS
    assert "select.remove()" not in SELECT_JS
    # Pages listen on the native element, so selection has to be announced there.
    assert 'select.dispatchEvent(new Event("change", { bubbles: true }))' in SELECT_JS


def test_it_follows_options_that_change_after_load():
    """Dependent selects hide options; modals fill them after a fetch."""
    assert "MutationObserver" in SELECT_JS
    assert '"hidden"' in SELECT_JS
    # `element.value = "x"` fires nothing — the setter is wrapped so the label
    # still repaints when a screen resets a filter.
    assert 'Object.defineProperty(select, "value"' in SELECT_JS


def test_modal_fields_are_enhanced_too():
    assert 'xselect.enhance(document.getElementById("app-modal-body"))' in APP_JS
    # And the modal focuses the visible control, not the hidden native one.
    assert "#app-modal-body .xselect-trigger" in APP_JS


def test_arabic_search_folds_what_a_typist_does_not_type():
    """احمد must find أحمد, and على must find علي."""
    for pattern in ("[أإآٱ]", "ة", "ى"):
        assert pattern in SELECT_JS


def test_it_can_be_opted_out_of():
    assert "dataset.noEnhance" in SELECT_JS
    assert "select.multiple" in SELECT_JS  # native multi-select is left alone


def test_the_caret_icon_exists():
    assert 'id="i-chevron"' in ICONS
    assert "#i-chevron" in SELECT_JS


def test_it_is_drawn_from_tokens_not_hard_coded_colours():
    """So it follows the centre's palette, dark mode and density like the rest."""
    body = "\n".join(line for line in SELECT_CSS.splitlines() if not line.strip().startswith("/*"))
    for token in ("--surface", "--ink-200", "--brand-400", "--radius"):
        assert token in body
    assert '[data-density="compact"]' in SELECT_CSS


def test_the_panel_sits_above_a_modal():
    """It is rendered outside the field, so its z-index has to clear 1055."""
    assert "z-index: 1080" in SELECT_CSS
    assert 'select.closest(".modal")' in SELECT_JS
