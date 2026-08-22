"""TASK-092 — host → tenant, or 404.

The middleware is written and proven here but is **not** in the default stack
yet: with it active every request needs a ``Domain`` row, and the domain models
do not carry a ``tenant`` column until Phase 14. TASK-104 ("Release C") inserts
it. Until then these tests install it themselves, which also keeps the ordering
assumptions of docs/10 §N.3 explicit rather than implied.
"""

import pytest
from django.core.cache import cache
from django.test import Client, override_settings

from apps.tenancy.constants import TenantStatus
from apps.tenancy.context import current_tenant
from apps.tenancy.models import Domain
from apps.tenancy.resolution import CACHE_PREFIX, cache_key, normalize_host, resolve_host

from .factories import make_tenant, make_two_tenants

# The production stack of docs/10 §N.3, with the three tenancy middlewares at
# positions [3], [8] and [11].
TENANT_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.tenancy.middleware.TenantResolutionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.tenancy.middleware.TenantSessionGuardMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.tenancy.middleware.TenantStatusMiddleware",
    "apps.core.middleware.AuditContextMiddleware",
    "apps.core.middleware.ForcePasswordChangeMiddleware",
]

tenant_stack = override_settings(
    MIDDLEWARE=TENANT_MIDDLEWARE,
    ALLOWED_HOSTS=[".testserver", "testserver", "console.testserver"],
    CONSOLE_HOST="console.testserver",
)

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Host normalisation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Alpha.TestServer", "alpha.testserver"),
        ("alpha.testserver:8000", "alpha.testserver"),
        ("  alpha.testserver  ", "alpha.testserver"),
        ("alpha.testserver.", "alpha.testserver"),
        ("[::1]:8000", "[::1]"),
        ("[2001:db8::1]", "[2001:db8::1]"),
        ("", ""),
    ],
)
def test_normalize_host(raw, expected):
    assert normalize_host(raw) == expected


# --------------------------------------------------------------------------- #
# Resolution + caching
# --------------------------------------------------------------------------- #


def test_resolve_host_finds_the_tenant():
    tenant = make_tenant("alpha", host="alpha.testserver")
    assert resolve_host("alpha.testserver") == tenant


def test_resolve_host_returns_none_for_an_unknown_host():
    assert resolve_host("nope.testserver") is None


def test_resolution_is_cached(django_assert_num_queries):
    make_tenant("alpha", host="alpha.testserver")
    cache.clear()

    resolve_host("alpha.testserver")  # warms it
    # One query remains: the tenant row itself, by cached primary key. The
    # Domain join — the part that would grow with the client list — is gone.
    with django_assert_num_queries(1):
        resolve_host("alpha.testserver")


def test_unknown_hosts_are_negatively_cached(django_assert_num_queries):
    """An unknown-host flood must not become a query per packet."""
    cache.clear()
    resolve_host("nope.testserver")
    with django_assert_num_queries(0):
        assert resolve_host("nope.testserver") is None


def test_adding_a_domain_invalidates_the_cache():
    tenant = make_tenant("alpha", host="alpha.testserver")
    assert resolve_host("extra.testserver") is None  # negative-cached
    Domain.objects.create(tenant=tenant, host="extra.testserver", is_custom=True)
    assert resolve_host("extra.testserver") == tenant


def test_deleting_a_domain_invalidates_the_cache():
    tenant = make_tenant("alpha", host="alpha.testserver")
    extra = Domain.objects.create(tenant=tenant, host="extra.testserver")
    assert resolve_host("extra.testserver") == tenant
    extra.delete()
    assert resolve_host("extra.testserver") is None


def test_suspending_a_tenant_invalidates_the_cache():
    """A suspension must bite now, not in five minutes."""
    tenant = make_tenant("alpha", host="alpha.testserver")
    assert resolve_host("alpha.testserver").is_operational is True
    tenant.status = TenantStatus.SUSPENDED
    tenant.save()
    assert resolve_host("alpha.testserver").is_operational is False


def test_cache_key_shape():
    assert cache_key("alpha.testserver") == f"{CACHE_PREFIX}alpha.testserver"


# --------------------------------------------------------------------------- #
# The request path
# --------------------------------------------------------------------------- #


@tenant_stack
def test_known_host_resolves_and_serves():
    make_tenant("alpha", host="alpha.testserver")
    response = Client().get("/healthz/", headers={"host": "alpha.testserver"})
    assert response.status_code == 200


@tenant_stack
def test_unknown_host_is_a_flat_404():
    """Never a redirect and never a 'no such center' page — both would confirm
    which hostnames exist, which is step one of enumerating the client list."""
    make_tenant("alpha", host="alpha.testserver")
    response = Client().get("/healthz/", headers={"host": "nope.testserver"})
    assert response.status_code == 404


@tenant_stack
def test_host_is_case_insensitive():
    make_tenant("alpha", host="alpha.testserver")
    response = Client().get("/healthz/", headers={"host": "ALPHA.testserver"})
    assert response.status_code == 200


@tenant_stack
def test_request_carries_the_tenant():
    tenant = make_tenant("alpha", host="alpha.testserver")
    seen = {}

    from apps.tenancy.middleware import TenantResolutionMiddleware

    def capture(request):
        from django.http import HttpResponse

        seen["tenant"] = request.tenant
        seen["context"] = current_tenant()
        return HttpResponse("ok")

    from django.test import RequestFactory

    request = RequestFactory().get("/", headers={"host": "alpha.testserver"})
    TenantResolutionMiddleware(capture)(request)

    assert seen["tenant"] == tenant
    assert seen["context"] == tenant


@tenant_stack
def test_context_is_reset_after_the_response():
    make_tenant("alpha", host="alpha.testserver")
    Client().get("/healthz/", headers={"host": "alpha.testserver"})
    assert current_tenant() is None


@tenant_stack
def test_context_is_reset_after_an_exception():
    """The `finally` is the whole point: a leaked tenant means the next request
    on this worker reads another center's data."""
    from django.http import HttpResponse
    from django.test import RequestFactory

    from apps.tenancy.middleware import TenantResolutionMiddleware

    make_tenant("alpha", host="alpha.testserver")

    def boom(request):
        raise RuntimeError("view exploded")
        return HttpResponse()  # pragma: no cover

    request = RequestFactory().get("/", headers={"host": "alpha.testserver"})
    with pytest.raises(RuntimeError):
        TenantResolutionMiddleware(boom)(request)
    assert current_tenant() is None


@tenant_stack
def test_two_hosts_are_served_independently():
    make_two_tenants()
    client = Client()
    assert client.get("/healthz/", headers={"host": "alpha.testserver"}).status_code == 200
    assert current_tenant() is None
    assert client.get("/healthz/", headers={"host": "beta.testserver"}).status_code == 200
    assert current_tenant() is None


# --------------------------------------------------------------------------- #
# The console host
# --------------------------------------------------------------------------- #


@tenant_stack
def test_console_host_swaps_the_urlconf():
    """A tenant host physically cannot route to a console view, because the two
    URLconfs are disjoint — not because a permission check says so."""
    from django.http import HttpResponse
    from django.test import RequestFactory

    from apps.tenancy.middleware import TenantResolutionMiddleware

    seen = {}

    def capture(request):
        seen["urlconf"] = getattr(request, "urlconf", None)
        seen["tenant"] = request.tenant
        seen["is_console"] = request.is_console
        return HttpResponse("ok")

    request = RequestFactory().get("/", headers={"host": "console.testserver"})
    TenantResolutionMiddleware(capture)(request)

    assert seen["urlconf"] == "cms.urls_console"
    assert seen["tenant"] is None
    assert seen["is_console"] is True


@tenant_stack
def test_console_host_needs_no_domain_row():
    response = Client().get("/healthz/", headers={"host": "console.testserver"})
    assert response.status_code == 200


@override_settings(MIDDLEWARE=TENANT_MIDDLEWARE, ALLOWED_HOSTS=["*"], CONSOLE_HOST="")
def test_empty_console_host_disables_console_routing():
    """An unset CONSOLE_HOST must not accidentally match a blank Host header."""
    make_tenant("alpha", host="alpha.testserver")
    assert Client().get("/healthz/", headers={"host": "console.testserver"}).status_code == 404


# --------------------------------------------------------------------------- #
# Suspension
# --------------------------------------------------------------------------- #


@tenant_stack
@pytest.mark.parametrize("status", [TenantStatus.SUSPENDED, TenantStatus.ARCHIVED])
def test_suspended_tenant_is_gated(status):
    make_tenant("alpha", host="alpha.testserver", status=status)
    response = Client().get("/", headers={"host": "alpha.testserver"})
    assert response.status_code == 403
    assert "tenancy/gate.html" in [t.name for t in response.templates]


@tenant_stack
@pytest.mark.parametrize("status", [TenantStatus.TRIAL, TenantStatus.ACTIVE, TenantStatus.PAST_DUE])
def test_operational_statuses_are_not_gated(status):
    make_tenant("alpha", host="alpha.testserver", status=status)
    response = Client().get("/healthz/", headers={"host": "alpha.testserver"})
    assert response.status_code == 200


@tenant_stack
def test_health_checks_stay_reachable_while_suspended():
    """Monitoring must be able to tell 'suspended' from 'down'."""
    make_tenant("alpha", host="alpha.testserver", status=TenantStatus.SUSPENDED)
    response = Client().get("/healthz/", headers={"host": "alpha.testserver"})
    assert response.status_code == 200


@tenant_stack
def test_suspension_gate_does_not_touch_data():
    """A door, not a shredder."""
    tenant = make_tenant("alpha", host="alpha.testserver", status=TenantStatus.SUSPENDED)
    Client().get("/", headers={"host": "alpha.testserver"})
    tenant.refresh_from_db()
    assert tenant.domains.count() == 1
    assert tenant.status == TenantStatus.SUSPENDED


# --------------------------------------------------------------------------- #
# TLS check (TASK-121 wires the proxy to it; the contract is fixed here)
# --------------------------------------------------------------------------- #


@tenant_stack
def test_tls_check_accepts_a_known_operational_host():
    make_tenant("alpha", host="alpha.testserver")
    response = Client().get(
        "/internal/tls-check/",
        {"domain": "alpha.testserver"},
        headers={"host": "alpha.testserver"},
    )
    assert response.status_code == 200


@tenant_stack
def test_tls_check_refuses_an_unknown_host():
    make_tenant("alpha", host="alpha.testserver")
    response = Client().get(
        "/internal/tls-check/",
        {"domain": "attacker.example.com"},
        headers={"host": "alpha.testserver"},
    )
    assert response.status_code == 404


@tenant_stack
def test_tls_check_refuses_a_suspended_tenant():
    make_tenant("alpha", host="alpha.testserver")
    beta = make_tenant("beta", host="beta.testserver", status=TenantStatus.SUSPENDED)
    assert beta.is_operational is False
    response = Client().get(
        "/internal/tls-check/",
        {"domain": "beta.testserver"},
        headers={"host": "alpha.testserver"},
    )
    assert response.status_code == 404
