"""The console's language switcher has to produce an English console.

Wiring a switcher into the topbar is the easy half. The half that actually
decides whether the control is honest is whether every screen behind it has a
msgid — and that is not something reading the diff can settle, because a single
forgotten literal renders Arabic in the middle of an English page and nothing
errors.

So this walks every console screen with the language set to English and fails
on any Arabic character left in the HTML. A new screen is covered the day it
joins PAGES; a forgotten `{% translate %}` fails the day it is written.
"""

import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.tenancy.tests.factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db

CONSOLE_HOST = "console.testserver"
PASSWORD = "TestPass!2026"
URLCONF = "cms.urls_console"

ARABIC = re.compile(r"[؀-ۿ]")

#: The switcher itself names each language in that language — "العربية" beside
#: "EN" — which is the point of it, and the one place Arabic on an English page
#: is correct rather than forgotten.
SWITCHER = re.compile(r"<form action=\"/i18n/setlang/\".*?</form>", re.S)

#: A `<script>` block is the other legitimate exception: its `gettext("…")`
#: arguments are msgids, translated in the browser against /jsi18n/, so they are
#: *meant* to sit in the source in Arabic. Dropping them here would lose the
#: coverage, so `test_every_javascript_message_is_translated` picks them up.
SCRIPT = re.compile(r"<script\b.*?</script>", re.S)
JS_MESSAGE = re.compile(r"""(?<![\w.])n?gettext\(\s*(["'])(.*?)\1""")


def body(html: str) -> str:
    return SCRIPT.sub("", SWITCHER.sub("", html))


#: Every console screen reachable with a GET. A screen missing from this list
#: is a screen nobody is checking.
PAGES = [
    ("console:dashboard", None),
    ("console:tenant_list", None),
    ("console:tenant_new", None),
    ("console:plan_list", None),
    ("console:audit", None),
    ("console:tenant_detail", "tenant"),
    ("console:tenant_features", "tenant"),
    ("console:plan_edit", "plan"),
    ("console:plan_new", None),
]


@pytest.fixture(autouse=True)
def hosts(settings):
    settings.CONSOLE_HOST = CONSOLE_HOST
    settings.TENANT_BASE_DOMAIN = "testserver"
    settings.CONSOLE_LANGUAGE = "ar"


@pytest.fixture
def operator():
    User.objects.create_user(
        username="operator", password=PASSWORD, is_platform_staff=True, full_name="Op"
    )
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    client.post(
        reverse("console:login", urlconf=URLCONF),
        {"username": "operator", "password": PASSWORD},
    )
    return client


@pytest.fixture
def subject():
    """A client and a plan, both named in ASCII.

    Their names are data, not interface: a center really called "سنتر ألفا"
    must keep its name on an English page, so naming them in Arabic here would
    make every assertion below fail for the one reason that is not a bug.
    """
    plan = make_plan("gold", name="Gold")
    return make_tenant("alpha", name="Alpha Center", plan=plan, host="alpha.testserver"), plan


def urls(tenant, plan):
    for name, argument in PAGES:
        args = {"tenant": [tenant.pk], "plan": [plan.pk], None: []}[argument]
        yield name, reverse(name, args=args, urlconf=URLCONF)


# --------------------------------------------------------------------------- #


def test_the_page_list_is_not_silently_empty(operator, subject):
    """Guard the guard: a signed-out client would 302 everywhere and the sweep
    below would pass while reading nine redirects."""
    tenant, plan = subject
    for name, url in urls(tenant, plan):
        assert operator.get(url).status_code == 200, name


@pytest.mark.parametrize("page", [name for name, _argument in PAGES])
def test_every_console_screen_renders_in_english(page, operator, subject, settings):
    tenant, plan = subject
    operator.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"

    url = dict(urls(tenant, plan))[page]
    html = operator.get(url).content.decode("utf-8")

    assert 'lang="en"' in html, f"{page} did not switch language at all"
    leftovers = sorted({line.strip() for line in body(html).split("\n") if ARABIC.search(line)})
    assert leftovers == [], (
        f"{page} is set to English but still renders Arabic. Each line below has "
        f"a literal that never reached the catalogue: {leftovers[:5]}"
    )


def test_the_console_topbar_offers_both_controls(operator, subject):
    """The controls themselves, not just the machinery behind them.

    Both were pinned on purpose once — the theme to light, the language to
    Arabic — so their absence looks deliberate rather than broken, and no other
    test would notice if an include were dropped.
    """
    tenant, _plan = subject
    html = operator.get(reverse("console:dashboard", urlconf=URLCONF)).content.decode("utf-8")

    assert "/i18n/setlang/" in html, "no language switcher in the topbar"
    assert 'id="theme-swatches"' in html, "no theme picker in the topbar"
    # theme.js reads these before first paint; without them the picker has no
    # baseline to reset to.
    assert "data-default-accent" in html
    assert "css/themes.css" in html and "js/theme.js" in html


def test_every_javascript_message_is_translated(operator, subject):
    """The half of each page `body()` had to drop.

    A `gettext("…")` whose argument is not a msgid in the catalogue returns its
    own argument — so the screen renders English until the first AJAX response
    lands and an Arabic toast appears in the middle of it. Nothing errors, and
    nothing in the template diff shows it.
    """
    from django.utils import translation

    tenant, plan = subject
    # Collected first, checked after: each request runs LocaleMiddleware, which
    # activates the console's own language and would leave it active — so a
    # check inside this loop would be asking the Arabic catalogue.
    found = []
    for page, url in urls(tenant, plan):
        html = operator.get(url).content.decode("utf-8")
        for script in SCRIPT.findall(html):
            found += [(page, msgid) for _q, msgid in JS_MESSAGE.findall(script)]

    with translation.override("en"):
        orphans = [
            f"{page}: {msgid}"
            for page, msgid in found
            if ARABIC.search(msgid) and translation.gettext(msgid) == msgid
        ]

    assert found, "no gettext() calls found at all — the scan is looking in the wrong place"
    assert orphans == [], (
        "these gettext() calls have no catalogue entry, so they render Arabic "
        f"on an English page: {orphans}"
    )


def test_the_login_page_renders_in_english(settings):
    """Signed out, where a half-translated page is the first thing seen."""
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"

    html = client.get(reverse("console:login", urlconf=URLCONF)).content.decode("utf-8")

    assert 'lang="en"' in html
    assert not ARABIC.search(body(html))


def test_the_sweep_would_notice_arabic(operator, subject, settings):
    """The other half of the matrix.

    Without it, a sweep that never actually finds Arabic looks identical to a
    sweep whose pages stopped rendering — so prove the same pages are full of
    Arabic when the console is left in its default language.
    """
    tenant, plan = subject
    html = operator.get(reverse("console:dashboard", urlconf=URLCONF)).content.decode("utf-8")
    assert ARABIC.search(body(html)), "the default console should be Arabic"
