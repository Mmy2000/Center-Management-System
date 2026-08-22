"""Every screen that fetches its own data has to say so while it is fetching.

The rule these tests hold: a page that reads over AJAX must either go through
`ui.table` / `ui.region` (skeletons, then rows or an error row) or be a silent
background poll (`quiet: true`). "Nothing on screen for a second, then rows"
is the one outcome that is not allowed.
"""

import pathlib
import re

import pytest
from django.conf import settings

TEMPLATES = pathlib.Path(settings.BASE_DIR) / "templates"
STATIC_JS = pathlib.Path(settings.BASE_DIR) / "static" / "js"

# The topbar's search drops results into a menu, not a table, and the global
# progress bar already covers it.
EXEMPT = {"partials/_topbar.html"}

LOADING_MARKERS = ("ui.table(", "ui.region(", "ui.skeletonRows(", "quiet: true")


def templates_that_fetch():
    for path in sorted(TEMPLATES.rglob("*.html")):
        relative = path.relative_to(TEMPLATES).as_posix()
        if relative in EXEMPT:
            continue
        if "http.get(" in path.read_text(encoding="utf-8"):
            yield relative, path


@pytest.mark.parametrize(
    "relative,path", list(templates_that_fetch()), ids=lambda value: str(value)
)
def test_a_page_that_fetches_shows_that_it_is_fetching(relative, path):
    source = path.read_text(encoding="utf-8")
    assert any(marker in source for marker in LOADING_MARKERS), (
        f"{relative} fetches data with no loading state — wrap the fetch in "
        "ui.table()/ui.region(), or mark it quiet if it is a background poll"
    )


def test_no_template_still_hand_rolls_a_loading_row():
    """The old '<td>جارٍ التحميل…</td>' placeholder flashed and then sat empty."""
    offenders = [
        path.relative_to(TEMPLATES).as_posix()
        for path in TEMPLATES.rglob("*.html")
        if re.search(r"<td[^>]*>\s*\{% translate \"جارٍ التحميل…\" %\}", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_every_request_drives_the_progress_bar():
    """http.js announces, ui.js listens — neither half is any use alone."""
    http_js = (STATIC_JS / "http.js").read_text(encoding="utf-8")
    ui_js = (STATIC_JS / "ui.js").read_text(encoding="utf-8")

    assert http_js.count('announce("http:start"') >= 2  # request() and html()
    assert http_js.count('announce("http:end"') >= 2
    assert 'addEventListener("http:start"' in ui_js
    assert 'addEventListener("http:end"' in ui_js


def test_a_background_poll_can_opt_out_of_the_bar():
    http_js = (STATIC_JS / "http.js").read_text(encoding="utf-8")
    assert "options.quiet" in http_js


def test_the_config_panels_paint_before_their_data_arrives():
    """crud.js drives the settings and structure screens."""
    crud_js = (STATIC_JS / "crud.js").read_text(encoding="utf-8")
    load = crud_js[crud_js.index("async function load()"):]
    assert load.index("skeleton();") < load.index("await http.get"), (
        "crud.panel must paint its skeleton before the fetch, not after"
    )


def test_no_template_comment_spans_two_lines():
    """`{# … #}` is single-line only — Django renders a wrapped one as text.

    Multi-line needs {% comment %}…{% endcomment %}. This is silent: the page
    still returns 200, with the comment printed on it.
    """
    wrapped = re.compile(r"\{#(?:(?!#\}).)*\n(?:(?!#\}).)*#\}", re.S)
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in wrapped.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{path.relative_to(TEMPLATES).as_posix()}:{line}")

    assert offenders == [], (
        "these {# #} comments wrap onto a second line and will be printed on the "
        f"page: {offenders}"
    )
