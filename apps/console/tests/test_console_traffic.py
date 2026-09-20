"""The monitoring screen and its controls (TASK-122).

The page itself is a shell — every figure on it arrives by AJAX — so this is
where its behaviour actually lives. Asserting against the markup would test the
shell rather than the answer, which is the same reason ``test_console_api.py``
gives for its own shape.

Two things get most of the attention here: that nobody outside platform staff
can reach any of it, and that every control writes the audit row an operator
will need months later when somebody asks why a center was cut off.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.tenancy import traffic
from apps.tenancy.constants import PlatformAction, TenantStatus, TrafficMode
from apps.tenancy.context import tenant_context
from apps.tenancy.models import PlatformAuditLog, TenantRatePolicy
from apps.tenancy.tests.factories import make_tenant, make_two_tenants

pytestmark = pytest.mark.django_db

CONSOLE_HOST = "console.testserver"
PASSWORD = "TestPass!2026"


@pytest.fixture(autouse=True)
def console_hosts(settings):
    settings.CONSOLE_HOST = CONSOLE_HOST
    settings.TENANT_BASE_DOMAIN = "testserver"


@pytest.fixture(autouse=True)
def _drain_buffer():
    """Drop, never flush — see the note on the same fixture in the tenancy suite."""
    traffic.discard()
    yield
    traffic.discard()


def url(name, *args):
    return reverse(f"console:{name}", args=args, urlconf="cms.urls_console")


@pytest.fixture
def operator():
    return User.objects.create_user(
        username="operator", password=PASSWORD, is_platform_staff=True, full_name="Op"
    )


@pytest.fixture
def console(operator):
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    assert (
        client.post(url("login"), {"username": "operator", "password": PASSWORD}).status_code == 302
    )
    return client


def post(client, name, *args, **payload):
    return client.post(
        url(name, *args),
        data=payload,
        content_type="application/json",
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )


# ---------------------------------------------------------------- the wall --


def test_the_page_needs_platform_staff(operator):
    """An anonymous operator is sent to the console's own sign-in, not a 500."""
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST

    response = client.get(url("traffic"))

    assert response.status_code == 302
    assert "/login/" in response["Location"]


def test_a_center_admin_cannot_sign_in_and_cannot_read_the_api(console_hosts):
    """The console's login is scoped to ``tenant IS NULL``, so they never get in.

    They are therefore refused as *anonymous* (403 from the shared ``ajax``
    envelope), not as themselves — which is the stronger answer: the console
    host cannot even tell them their password was right.
    """
    tenant = make_tenant("alpha")
    with tenant_context(tenant):
        User.objects.create_user(username="boss", password=PASSWORD, role=Role.CENTER_ADMIN)

    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    assert client.post(url("login"), {"username": "boss", "password": PASSWORD}).status_code == 401

    response = client.get(url("api_traffic"))
    assert response.status_code == 403
    assert response.json()["code"] == "ERR_AUTH_REQUIRED"


def test_a_platform_user_who_is_not_staff_is_signed_out_and_refused(console_hosts):
    """Three walls, and the outermost one answers first.

    ``TenantSessionGuardMiddleware`` flushes any session on the console host
    that does not belong to platform staff, so this user never reaches either
    the view's ``platform_staff_required`` or ``console_ajax``'s own check —
    they arrive anonymous. Those two inner walls stay regardless: a wall you
    only built once is a wall you are trusting too much.
    """
    helper = User.objects.create_user(username="helper", password=PASSWORD, is_platform_staff=False)

    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    client.force_login(helper)

    api = client.get(url("api_traffic"))
    assert api.status_code == 403
    assert api.json()["code"] == "ERR_AUTH_REQUIRED"

    page = client.get(url("traffic"))
    assert page.status_code == 302
    assert "/login/" in page["Location"]


def test_console_ajax_refuses_a_non_staff_user_with_404(rf, console_hosts):
    """The inner wall, tested directly because the outer one hides it.

    404 rather than 403 on purpose: somebody who got this far should not have
    it confirmed that the endpoint exists.
    """
    from apps.console import api as console_api

    helper = User.objects.create_user(
        username="helper2", password=PASSWORD, is_platform_staff=False
    )
    request = rf.get("/api/traffic/")
    request.is_console = True
    request.user = helper

    assert console_api.traffic_overview(request).status_code == 404


def test_a_tenant_host_has_no_route_to_the_monitoring_page():
    """The structural wall: the console's URLconf is not mounted on a center."""
    tenant = make_tenant("alpha")
    client = Client()
    client.defaults["HTTP_HOST"] = tenant.primary_host

    assert client.get("/traffic/").status_code == 404


def test_controls_reject_a_get(console):
    tenant = make_tenant("alpha")

    response = console.get(url("api_tenant_traffic_policy", tenant.pk))

    assert response.status_code == 405


# ----------------------------------------------------------------- reading --


def test_the_overview_lists_every_client_with_its_figures(console):
    alpha, beta = make_two_tenants()
    for _ in range(4):
        traffic.record(alpha.pk, 200, 20)
    traffic.record(beta.pk, 500, 300)
    traffic.flush()

    payload = console.get(url("api_traffic")).json()["data"]

    assert payload["count"] == 2
    rows = {row["slug"]: row for row in payload["results"]}
    assert rows["alpha"]["period_total"] == 4
    assert rows["alpha"]["ok"] == 4
    assert rows["beta"]["c5"] == 1
    assert rows["beta"]["error_rate"] == 100.0
    assert payload["totals"]["requests"] == 5
    assert payload["totals"]["errors"] == 1


def test_a_client_with_no_traffic_still_appears(console):
    """Disappearing when idle would make "is it down?" unanswerable."""
    make_tenant("alpha")

    payload = console.get(url("api_traffic")).json()["data"]

    assert payload["count"] == 1
    assert payload["results"][0]["period_total"] == 0
    assert payload["results"][0]["mode"] == TrafficMode.ACTIVE


def test_rows_are_ordered_by_who_is_making_the_noise(console):
    """Alphabetical order buries the one row anyone opened this page for."""
    quiet = make_tenant("aaa-quiet")
    loud = make_tenant("zzz-loud")
    for _ in range(50):
        traffic.record(loud.pk, 200, 1)
    traffic.record(quiet.pk, 200, 1)
    traffic.flush()

    rows = console.get(url("api_traffic")).json()["data"]["results"]

    assert [row["slug"] for row in rows] == ["zzz-loud", "aaa-quiet"]


def test_the_mode_filter_narrows_to_controlled_clients(console):
    make_tenant("alpha")
    limited = make_tenant("beta")
    TenantRatePolicy.objects.create(tenant=limited, max_rps=5)

    rows = console.get(url("api_traffic"), {"mode": TrafficMode.LIMITED}).json()["data"]["results"]

    assert [row["slug"] for row in rows] == ["beta"]


def test_active_only_hides_the_silent_majority(console):
    busy = make_tenant("alpha")
    make_tenant("beta")
    traffic.record(busy.pk, 200, 1)
    traffic.flush()

    rows = console.get(url("api_traffic"), {"active_only": "1"}).json()["data"]["results"]

    assert [row["slug"] for row in rows] == ["alpha"]


def test_an_unknown_period_falls_back_rather_than_failing(console):
    make_tenant("alpha")

    payload = console.get(url("api_traffic"), {"minutes": "999999"}).json()["data"]

    assert payload["minutes"] == 5


def test_usage_against_the_limit_names_the_window_that_binds(console):
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, max_rpm=10)
    for _ in range(9):
        traffic.record(tenant.pk, 200, 1)
    traffic.flush()

    row = console.get(url("api_traffic")).json()["data"]["results"][0]

    assert row["pressure"]["window"] == "rpm"
    assert row["pressure"]["limit"] == 10


def test_the_detail_endpoint_carries_a_full_hour_timeline(console):
    tenant = make_tenant("alpha")
    traffic.record(tenant.pk, 200, 5)
    traffic.record(tenant.pk, 500, 5)
    traffic.flush()

    payload = console.get(url("api_tenant_traffic", tenant.pk)).json()["data"]

    assert payload["tenant"]["slug"] == "alpha"
    assert len(payload["timeline"]) == 60
    newest = payload["timeline"][-1]
    assert (newest["ok"], newest["c5"]) == (1, 1)


def test_every_row_carries_a_sparkline(console):
    tenant = make_tenant("alpha")
    traffic.record(tenant.pk, 200, 5)
    traffic.flush()

    row = console.get(url("api_traffic")).json()["data"]["results"][0]

    assert len(row["spark"]) == 30
    assert row["spark"][-1] == 1
    assert sum(row["spark"]) == 1


def test_a_one_minute_period_still_returns_a_full_sparkline(console):
    """Sharpening the numbers must not flatten the chart beside them."""
    make_tenant("alpha")

    payload = console.get(url("api_traffic"), {"minutes": 1}).json()["data"]

    assert payload["minutes"] == 1
    assert len(payload["results"][0]["spark"]) == 30
    assert len(payload["timeline"]) == 30


def test_the_timeline_sums_every_visible_tenant(console):
    alpha, beta = make_two_tenants()
    traffic.record(alpha.pk, 200, 5)
    traffic.record(alpha.pk, 404, 5)
    traffic.record(beta.pk, 500, 5)
    traffic.flush()

    newest = console.get(url("api_traffic")).json()["data"]["timeline"][-1]

    assert (newest["ok"], newest["c4"], newest["c5"]) == (1, 1, 1)


def test_the_timeline_follows_the_filters(console):
    """Filtering the table must redraw the chart against the same slice."""
    alpha, beta = make_two_tenants()
    TenantRatePolicy.objects.create(tenant=beta, max_rps=5)
    traffic.record(alpha.pk, 200, 5)
    traffic.record(beta.pk, 200, 5)
    traffic.flush()

    everything = console.get(url("api_traffic")).json()["data"]
    limited = console.get(url("api_traffic"), {"mode": TrafficMode.LIMITED}).json()["data"]

    assert everything["timeline"][-1]["ok"] == 2
    assert limited["timeline"][-1]["ok"] == 1


def test_the_timeline_is_oldest_first(console):
    make_tenant("alpha")

    timeline = console.get(url("api_traffic")).json()["data"]["timeline"]

    minutes = [point["minute"] for point in timeline]
    assert minutes == sorted(minutes)


def test_the_overview_reports_whether_its_own_numbers_are_trustworthy(console):
    """The LocMem caveat travels with the data, not only in the template."""
    make_tenant("alpha")

    payload = console.get(url("api_traffic")).json()["data"]

    assert payload["cache_shared"] is False  # test settings use LocMemCache
    assert set(payload["server"]) == {"load1", "cpu_percent", "memory_percent", "cpus"}


def test_the_overview_does_not_cost_a_query_per_client(console, django_assert_max_num_queries):
    """The property that keeps this page from becoming the load it reports.

    Ten clients, and the query count must not move with the number of them:
    the tenants, their domains, their policies — and every counter for all of
    them in one ``get_many``.
    """
    for index in range(10):
        make_tenant(f"t{index}")

    with django_assert_max_num_queries(8):
        console.get(url("api_traffic"))


# ----------------------------------------------------------------- control --


def test_setting_limits_creates_the_row_and_audits_it(console, operator):
    tenant = make_tenant("alpha")

    response = post(
        console,
        "api_tenant_traffic_policy",
        tenant.pk,
        action="limits",
        max_rps=10,
        burst=5,
        reason="سكربت يستهلك الخادم",
    )

    assert response.status_code == 200
    policy = TenantRatePolicy.objects.get(tenant=tenant)
    assert (policy.max_rps, policy.burst) == (10, 5)
    assert policy.mode == TrafficMode.LIMITED
    assert policy.updated_by == operator

    entry = PlatformAuditLog.objects.get(action=PlatformAction.TRAFFIC_LIMITED)
    assert entry.tenant == tenant
    assert entry.actor == operator
    assert entry.reason == "سكربت يستهلك الخادم"
    assert entry.changes["max_rps"] == [None, 10]


def test_clearing_every_limit_removes_the_row(console):
    """ "Unlimited" gets exactly one representation: no row."""
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, max_rps=10)

    post(console, "api_tenant_traffic_policy", tenant.pk, action="limits", reason="انتهى الأمر")

    assert not TenantRatePolicy.objects.filter(tenant=tenant).exists()
    assert traffic.policy_for(tenant.pk) == traffic.UNLIMITED


def test_burst_without_a_per_second_limit_is_a_field_error(console):
    tenant = make_tenant("alpha")

    response = post(
        console, "api_tenant_traffic_policy", tenant.pk, action="limits", burst=5, reason="x"
    )

    assert response.status_code == 400
    assert "burst" in response.json()["field_errors"]
    assert not TenantRatePolicy.objects.filter(tenant=tenant).exists()


def test_a_nonsense_limit_is_refused_rather_than_stored(console):
    tenant = make_tenant("alpha")

    response = post(console, "api_tenant_traffic_policy", tenant.pk, action="limits", max_rps="abc")

    assert response.status_code == 400
    assert "max_rps" in response.json()["field_errors"]


def test_an_absurd_limit_is_refused_as_a_typo(console):
    tenant = make_tenant("alpha")

    response = post(
        console, "api_tenant_traffic_policy", tenant.pk, action="limits", max_rps=10**12
    )

    assert response.status_code == 400


def test_blocking_requires_a_reason(console):
    tenant = make_tenant("alpha")

    response = post(console, "api_tenant_traffic_policy", tenant.pk, action="block", reason="لا")

    assert response.status_code == 400
    assert "reason" in response.json()["field_errors"]
    assert not TenantRatePolicy.objects.filter(tenant=tenant, blocked=True).exists()


def test_blocking_is_audited_and_bites_immediately(console, operator):
    tenant = make_tenant("alpha")

    post(
        console,
        "api_tenant_traffic_policy",
        tenant.pk,
        action="block",
        reason="فيضان طلبات من سكربت",
    )

    assert traffic.policy_for(tenant.pk).blocked is True
    entry = PlatformAuditLog.objects.get(action=PlatformAction.TRAFFIC_BLOCKED)
    assert entry.actor == operator
    assert entry.reason == "فيضان طلبات من سكربت"

    center = Client()
    center.defaults["HTTP_HOST"] = tenant.primary_host
    assert center.get("/").status_code == 429


def test_unblocking_is_audited_and_restores_service(console):
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, blocked=True, blocked_reason="flood")

    post(console, "api_tenant_traffic_policy", tenant.pk, action="unblock", reason="تم الإصلاح")

    assert traffic.policy_for(tenant.pk).blocked is False
    assert PlatformAuditLog.objects.filter(action=PlatformAction.TRAFFIC_UNBLOCKED).exists()

    center = Client()
    center.defaults["HTTP_HOST"] = tenant.primary_host
    assert center.get("/").status_code != 429


def test_unblocking_keeps_a_limit_that_was_already_there(console):
    """Lifting a block must not silently remove the throttle underneath it."""
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, blocked=True, blocked_reason="flood", max_rps=5)

    post(console, "api_tenant_traffic_policy", tenant.pk, action="unblock", reason="تم")

    policy = traffic.policy_for(tenant.pk)
    assert policy.blocked is False
    assert policy.max_rps == 5


def test_blocking_twice_writes_one_audit_row(console):
    """An idempotent control should not manufacture a history of its own."""
    tenant = make_tenant("alpha")

    for _ in range(2):
        post(console, "api_tenant_traffic_policy", tenant.pk, action="block", reason="فيضان طلبات")

    assert PlatformAuditLog.objects.filter(action=PlatformAction.TRAFFIC_BLOCKED).count() == 1


def test_an_unknown_action_is_refused(console):
    tenant = make_tenant("alpha")

    response = post(console, "api_tenant_traffic_policy", tenant.pk, action="detonate")

    assert response.status_code == 400


def test_controlling_one_client_never_touches_another(console):
    """The isolation promise, at the level an operator actually acts on."""
    alpha, beta = make_two_tenants()

    post(console, "api_tenant_traffic_policy", alpha.pk, action="block", reason="فيضان طلبات")
    post(console, "api_tenant_traffic_policy", alpha.pk, action="limits", max_rps=1, reason="حد")

    assert traffic.policy_for(beta.pk) == traffic.UNLIMITED
    assert not TenantRatePolicy.objects.filter(tenant=beta).exists()

    center = Client()
    center.defaults["HTTP_HOST"] = beta.primary_host
    assert center.get("/").status_code != 429


def test_traffic_control_does_not_touch_the_subscription(console):
    """Throttling is not a billing event and must not read like one."""
    tenant = make_tenant("alpha", status=TenantStatus.ACTIVE)

    post(console, "api_tenant_traffic_policy", tenant.pk, action="block", reason="فيضان طلبات")

    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.ACTIVE
    assert tenant.suspended_at is None
    assert not PlatformAuditLog.objects.filter(action=PlatformAction.TENANT_SUSPENDED).exists()


def test_the_page_renders_for_an_operator(console):
    make_tenant("alpha")

    response = console.get(url("traffic"))

    assert response.status_code == 200
    assert "console/traffic.html" in [template.name for template in response.templates]


def test_the_page_carries_the_chart_plumbing(console):
    """The shell has to contain the hooks the JS fills, or the page is blank.

    Markup assertions are usually the wrong test for an AJAX screen — but
    these four are the contract between the template and ``charts.js``, and a
    rename on either side produces a silently empty card rather than an error.
    """
    make_tenant("alpha")

    html = console.get(url("traffic")).content.decode()

    assert "js/charts.js" in html
    assert 'id="timeline"' in html  # the chart mounts here
    assert 'id="legend"' in html  # three series always get a legend
    assert 'id="timeline-body"' in html  # the table twin every chart needs
