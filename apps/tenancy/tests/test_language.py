"""Which language a page renders in (LanguageMiddleware).

This has its own file because the first attempt at it was wrong in a way no
test caught: the override ran *before* ``LocaleMiddleware``, which then called
``translation.activate()`` and overwrote it. Everything passed, and the console
quietly rendered Arabic headings beside English form labels.

So these tests assert the *rendered output*, not the middleware's intent.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.tenancy.context import tenant_context
from apps.tenancy.tests.factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db

CONSOLE_HOST = "console.testserver"
PASSWORD = "TestPass!2026"


@pytest.fixture(autouse=True)
def hosts(settings):
    settings.CONSOLE_HOST = CONSOLE_HOST
    settings.TENANT_BASE_DOMAIN = "testserver"
    settings.CONSOLE_LANGUAGE = "ar"


@pytest.fixture
def console():
    User.objects.create_user(
        username="operator", password=PASSWORD, is_platform_staff=True, full_name="Op"
    )
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    client.post(
        reverse("console:login", urlconf="cms.urls_console"),
        {"username": "operator", "password": PASSWORD},
    )
    return client


def center(language="ar", slug="alpha"):
    """A signed-in center whose default language is ``language``."""
    from django.core.management import call_command

    from apps.accounts.services import sync_user_group

    tenant = make_tenant(slug, plan=make_plan("full"), host=f"{slug}.testserver", language=language)
    with tenant_context(tenant):
        call_command("seed_roles", verbosity=0)
        user = User.objects.create_user(username="boss", password=PASSWORD, role=Role.SUPER_ADMIN)
        sync_user_group(user)

    client = Client()
    client.defaults["HTTP_HOST"] = f"{slug}.testserver"
    with tenant_context(tenant):
        assert client.login(username="boss", password=PASSWORD)
    return tenant, client


# --------------------------------------------------------------------------- #
# The console is pinned
# --------------------------------------------------------------------------- #


def test_the_console_defaults_to_arabic(console, settings):
    """No cookie, no negotiation with the browser: CONSOLE_LANGUAGE decides.

    Without this the browser's Accept-Language chose, and an operator on an
    English browser met Arabic headings beside English model labels.
    """
    plan = make_plan("gold")
    html = console.get(
        reverse("console:plan_edit", args=[plan.pk], urlconf="cms.urls_console")
    ).content.decode("utf-8")

    assert "الاسم" in html
    assert "Monthly price" not in html


def test_an_operator_can_switch_the_console_to_english(console, settings):
    """The default is a default, not a lock — the switcher has to actually
    switch, or offering the control is worse than not offering it."""
    plan = make_plan("gold")
    url = reverse("console:plan_edit", args=[plan.pk], urlconf="cms.urls_console")

    console.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    html = console.get(url).content.decode("utf-8")

    assert 'lang="en"' in html
    assert 'dir="ltr"' in html


def test_switching_back_to_arabic_restores_rtl(console, settings):
    plan = make_plan("gold")
    url = reverse("console:plan_edit", args=[plan.pk], urlconf="cms.urls_console")

    console.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"
    html = console.get(url).content.decode("utf-8")
    assert 'lang="ar"' in html
    assert 'dir="rtl"' in html


def test_the_console_login_defaults_to_arabic(settings):
    """Signed out is where a wrong language is most visible."""
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    html = client.get(reverse("console:login", urlconf="cms.urls_console")).content.decode("utf-8")
    assert "تسجيل الدخول" in html


def test_an_empty_console_language_leaves_it_alone(settings, console):
    """The pin is a setting, not a law — clearing it hands the decision back."""
    settings.CONSOLE_LANGUAGE = ""
    console.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
    response = console.get(reverse("console:dashboard", urlconf="cms.urls_console"))
    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# A center's default is a fallback, never a command
# --------------------------------------------------------------------------- #


def test_a_center_gets_its_own_default_language(settings):
    _tenant, client = center(language="en")
    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert 'lang="en"' in html
    assert 'dir="ltr"' in html


def test_the_users_own_choice_beats_the_centers_default(settings):
    """The switcher writes a cookie; a center default that overrode it would
    break the bilingual UI the product ships."""
    _tenant, client = center(language="en")
    client.cookies[settings.LANGUAGE_COOKIE_NAME] = "ar"

    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert 'lang="ar"' in html
    assert 'dir="rtl"' in html


def test_the_language_switcher_still_works(settings):
    """The regression this fix could most easily have caused."""
    _tenant, client = center(language="ar")

    client.post(reverse("set_language"), {"language": "en", "next": "/"})
    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert 'lang="en"' in html

    client.post(reverse("set_language"), {"language": "ar", "next": "/"})
    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert 'lang="ar"' in html


def test_two_centers_can_default_to_different_languages(settings):
    _arabic, arabic_client = center(language="ar", slug="arabiccenter")
    _english, english_client = center(language="en", slug="englishcenter")

    assert 'lang="ar"' in arabic_client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert 'lang="en"' in english_client.get(reverse("dashboard:home")).content.decode("utf-8")


def test_a_center_with_no_language_set_falls_back_to_the_site_default(settings):
    tenant, client = center(language="ar")
    tenant.language = ""
    tenant.save(update_fields=["language"])

    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert f'lang="{settings.LANGUAGE_CODE}"' in html
