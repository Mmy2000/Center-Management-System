"""The console mounted at a path, for hosting with one hostname.

This mode exists for PythonAnywhere's free tier and anything like it: one host,
so no second host to put the console on. It is a real reduction in safety — the
console and the center now share an origin, so the structural wall is gone and
``platform_staff_required`` is the only one left. These tests pin down exactly
what survives that trade, because "one wall instead of two" is only acceptable
if the remaining wall actually holds.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.tenancy.context import tenant_context
from apps.tenancy.tests.factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db

PREFIX = "platform"
PASSWORD = "TestPass!2026"
HOST = "onlyhost.testserver"


@pytest.fixture(autouse=True)
def single_host(settings):
    """One hostname, a path-mounted console, and no console host at all."""
    settings.CONSOLE_HOST = ""
    settings.CONSOLE_PATH_PREFIX = PREFIX
    settings.TENANT_BASE_DOMAIN = "testserver"


@pytest.fixture
def tenant():
    return make_tenant("only", plan=make_plan("full"), host=HOST)


@pytest.fixture
def operator(tenant):
    return User.objects.create_user(
        username="operator", password=PASSWORD, is_platform_staff=True, full_name="Op"
    )


@pytest.fixture
def console(operator):
    client = Client()
    client.defaults["HTTP_HOST"] = HOST
    response = client.post(f"/{PREFIX}/login/", {"username": "operator", "password": PASSWORD})
    assert response.status_code == 302, "operator could not sign in"
    return client


@pytest.fixture
def center_client(tenant):
    """A center's own admin, signed in on the same host."""
    from django.core.management import call_command

    from apps.accounts.services import sync_user_group

    with tenant_context(tenant):
        call_command("seed_roles", verbosity=0)
        user = User.objects.create_user(username="boss", password=PASSWORD, role=Role.SUPER_ADMIN)
        sync_user_group(user)

    client = Client()
    client.defaults["HTTP_HOST"] = HOST
    with tenant_context(tenant):
        assert client.login(username="boss", password=PASSWORD)
    return client


# --------------------------------------------------------------------------- #
# The console is reachable at the prefix
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ["", "tenants/", "plans/", "audit/"])
def test_the_console_answers_under_the_prefix(path, console):
    assert console.get(f"/{PREFIX}/{path}").status_code == 200


def test_urls_reverse_with_the_prefix(console):
    """Reversing has to produce the prefixed path, or every link on every
    console page points at the center's site instead."""
    url = reverse("console:tenant_list", urlconf="cms.urls_console_path")
    assert url == f"/{PREFIX}/tenants/"
    assert console.get(url).status_code == 200


def test_the_console_pages_link_to_themselves(console):
    html = console.get(f"/{PREFIX}/").content.decode("utf-8")
    assert f'href="/{PREFIX}/tenants/"' in html
    assert 'href="/tenants/"' not in html


def test_the_json_api_works_under_the_prefix(console):
    response = console.get(f"/{PREFIX}/api/tenants/")
    assert response.status_code == 200
    assert response.json()["ok"] is True


# --------------------------------------------------------------------------- #
# The center is untouched
# --------------------------------------------------------------------------- #


def test_the_center_still_owns_the_rest_of_the_site(center_client):
    """Mounting the console at a path must not shadow a single tenant URL."""
    for name in ("dashboard:home", "students:list", "payments:workspace"):
        assert center_client.get(reverse(name)).status_code == 200


def test_a_center_admin_reaches_no_console_data(center_client):
    """The remaining wall, stated exactly.

    On the console the tenant-scoped user manager simply cannot see them — a
    user with a tenant does not exist in a tenantless context — so they arrive
    anonymous and meet the console's own sign-in, which their credentials do
    not open.

    Note what this mode *does* give away, and host-based routing does not: a
    login page rather than a flat 404, so a center admin can tell the console
    exists. That is the wall being traded, and it is why the prefix is opt-in
    and documented as single-host hosting only. What they cannot get is a
    single row of console data.
    """
    for path in ("", "tenants/", "plans/"):
        response = center_client.get(f"/{PREFIX}/{path}")
        assert response.status_code in (302, 404)
        if response.status_code == 302:
            assert f"/{PREFIX}/login/" in response["Location"]

    # The JSON layer answers nothing at all, in any circumstance.
    api = center_client.get(f"/{PREFIX}/api/tenants/")
    assert api.status_code in (302, 403, 404)
    assert b"slug" not in api.content


def test_a_center_admin_is_not_signed_out_by_wandering_in(center_client):
    """Sharing an origin means a mistyped URL is one of their own users on
    their own site — not a cross-host session to be flushed."""
    center_client.get(f"/{PREFIX}/tenants/")
    assert "_auth_user_id" in center_client.session
    assert center_client.get(reverse("dashboard:home")).status_code == 200


def test_an_anonymous_visitor_is_sent_to_the_console_login():
    client = Client()
    client.defaults["HTTP_HOST"] = HOST
    response = client.get(f"/{PREFIX}/tenants/")
    assert response.status_code == 302
    assert f"/{PREFIX}/login/" in response["Location"]


def test_the_center_login_and_the_console_login_are_different_doors(tenant):
    """Same host, two sign-ins: a center's staff cannot use the console's."""
    with tenant_context(tenant):
        User.objects.create_user(username="boss", password=PASSWORD, role=Role.SUPER_ADMIN)

    client = Client()
    client.defaults["HTTP_HOST"] = HOST
    assert (
        client.post(f"/{PREFIX}/login/", {"username": "boss", "password": PASSWORD}).status_code
        == 401
    )


# --------------------------------------------------------------------------- #
# The prefix is opt-in
# --------------------------------------------------------------------------- #


def test_no_prefix_means_no_path_console(settings, operator):
    """Production leaves it empty, and then the path is just a tenant 404."""
    settings.CONSOLE_PATH_PREFIX = ""
    client = Client()
    client.defaults["HTTP_HOST"] = HOST
    client.force_login(operator)
    assert client.get(f"/{PREFIX}/tenants/").status_code == 404


def test_a_similar_looking_path_is_not_the_console(console):
    """`/platformer/` is a tenant URL, not a console one."""
    assert console.get("/platformer/").status_code == 404


def test_the_console_still_works_by_host_when_both_are_set(settings, operator):
    """Setting a prefix must not break the hostname route for anyone who has
    both — a paid account moving off the free tier, say."""
    settings.CONSOLE_HOST = "console.testserver"
    make_tenant("other", plan=make_plan("full"), host="other.testserver")

    client = Client()
    client.defaults["HTTP_HOST"] = "console.testserver"
    client.force_login(operator)
    assert client.get("/tenants/").status_code == 200
