"""The console's JSON layer — the same envelope the product uses (docs/05 §G.2).

These screens are AJAX, so this is where their behaviour actually lives: the
templates are shells, and asserting against markup would test the shell rather
than the answer.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.tenancy.constants import FeatureState, TenantStatus
from apps.tenancy.context import tenant_context
from apps.tenancy.models import PlatformAuditLog
from apps.tenancy.resolver import has_feature
from apps.tenancy.tests.factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db

CONSOLE_HOST = "console.testserver"
PASSWORD = "TestPass!2026"


@pytest.fixture(autouse=True)
def console_hosts(settings):
    settings.CONSOLE_HOST = CONSOLE_HOST
    settings.TENANT_BASE_DOMAIN = "testserver"


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


# --------------------------------------------------------------------- auth --


def test_login_answers_the_envelope_for_ajax(operator):
    """The login page posts JSON so it can show the error without a reload."""
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    response = client.post(
        url("login"),
        data={"username": "operator", "password": PASSWORD},
        content_type="application/json",
        headers={"x-requested-with": "XMLHttpRequest"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["next"].endswith("/")


def test_a_bad_login_answers_401_with_one_message(operator):
    """One message for every failure — unknown user, wrong password, a center's
    account. Distinguishing them would enumerate the platform's usernames."""
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    response = client.post(
        url("login"),
        data={"username": "operator", "password": "wrong"},
        content_type="application/json",
        headers={"x-requested-with": "XMLHttpRequest"},
    )
    assert response.status_code == 401
    assert response.json()["ok"] is False


def test_a_plain_form_post_still_works(operator):
    """No JavaScript, no problem: the same view renders the page with the error."""
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    assert client.post(url("login"), {"username": "operator", "password": "no"}).status_code == 401
    assert (
        client.post(url("login"), {"username": "operator", "password": PASSWORD}).status_code == 302
    )


@pytest.mark.parametrize("name", ["api_tenants", "api_overview", "api_slug_check"])
def test_the_api_is_unreachable_from_a_tenant_host(name, operator):
    make_tenant("alpha", host="alpha.testserver")
    client = Client()
    client.defaults["HTTP_HOST"] = "alpha.testserver"
    client.force_login(operator)
    assert client.get(url(name)).status_code == 404


def test_the_api_refuses_a_non_platform_user():
    tenant = make_tenant("alpha", host="alpha.testserver")
    with tenant_context(tenant):
        user = User.objects.create_user(username="boss", password=PASSWORD)
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    client.force_login(user)
    # The session guard signs them out here, so they never reach the view.
    assert client.get(url("api_tenants")).status_code in (403, 404)


# -------------------------------------------------------------------- list --


def test_the_list_carries_what_the_table_draws(console):
    tenant = make_tenant("alpha", plan=make_plan("basic", max_students=100))
    row = console.get(url("api_tenants")).json()["data"]["results"][0]
    for key in (
        "id",
        "slug",
        "name",
        "host",
        "status",
        "status_label",
        "plan",
        "students",
        "users",
        "pressure",
        "days_left",
    ):
        assert key in row, key
    assert row["id"] == tenant.pk


def test_pressure_is_the_worst_limit_not_the_average(console):
    """A client at 100% of one limit and 0% of three others is *full*, and the
    console has to say so rather than reporting a comfortable 25%."""
    from apps.academics.models import EducationalStage, Grade
    from apps.students.services import create_student

    tenant = make_tenant("alpha", plan=make_plan("tight", max_students=2, max_cards=1000))
    with tenant_context(tenant):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        create_student(full_name="أ", grade=grade)
        create_student(full_name="ب", grade=grade)

    console.post(url("api_tenant_usage", tenant.pk))
    row = console.get(url("api_tenants")).json()["data"]["results"][0]
    assert row["pressure"] == 100


def test_an_unlimited_plan_reports_no_pressure(console):
    make_tenant("alpha", plan=make_plan("full"))
    assert console.get(url("api_tenants")).json()["data"]["results"][0]["pressure"] == 0


# ---------------------------------------------------------------- overview --


def test_the_overview_totals_across_clients(console):
    from apps.academics.models import EducationalStage, Grade
    from apps.students.services import create_student

    for slug in ("alpha", "beta"):
        tenant = make_tenant(slug, plan=make_plan("full"), host=f"{slug}.testserver")
        with tenant_context(tenant):
            stage = EducationalStage.objects.create(name="S", code="S", order=1)
            grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
            create_student(full_name="طالب", grade=grade)
        console.post(url("api_tenant_usage", tenant.pk))

    data = console.get(url("api_overview")).json()["data"]
    assert data["total"] == 2
    assert data["students"] == 2


def test_the_overview_lists_what_is_expiring(console):
    from django.utils import timezone

    make_tenant(
        "soon",
        plan=make_plan("full"),
        status=TenantStatus.ACTIVE,
        expires_at=timezone.now() + timezone.timedelta(days=5),
    )
    make_tenant("later", plan=make_plan("full"), status=TenantStatus.ACTIVE)

    data = console.get(url("api_overview")).json()["data"]
    assert [row["slug"] for row in data["expiring"]] == ["soon"]
    assert data["expiring"][0]["days_left"] == 4


# ------------------------------------------------------------------ status --


def test_suspending_over_ajax_needs_a_reason(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.post(
        url("api_tenant_status", tenant.pk),
        data={"action": "suspend", "reason": ""},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "reason" in response.json()["field_errors"]
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.ACTIVE


def test_suspending_returns_the_new_state(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.post(
        url("api_tenant_status", tenant.pk),
        data={"action": "suspend", "reason": "لم يتم السداد"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["data"]["tenant"]["status"] == "SUSPENDED"
    assert PlatformAuditLog.objects.filter(tenant=tenant).exists()


def test_an_unknown_action_is_refused(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.post(
        url("api_tenant_status", tenant.pk),
        data={"action": "delete_everything", "reason": "because"},
        content_type="application/json",
    )
    assert response.status_code == 400
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.ACTIVE


# ------------------------------------------------------------------- plans --


def test_a_plan_change_can_be_previewed_without_applying_it(console):
    """The warning has to arrive *before* the change, or the operator learns
    about it from the client."""
    tenant = make_tenant("alpha", plan=make_plan("full"))
    basic = make_plan("basic", features=["students", "academics", "attendance.scan"])

    response = console.post(
        url("api_tenant_plan", tenant.pk),
        data={"plan": basic.pk, "preview": True},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["data"]["warnings"]

    tenant.refresh_from_db()
    assert tenant.plan.slug == "full", "a preview must not change anything"


def test_applying_the_plan_change_moves_the_client(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    basic = make_plan("basic", features=["students"])
    console.post(
        url("api_tenant_plan", tenant.pk),
        data={"plan": basic.pk, "reason": "downgrade"},
        content_type="application/json",
    )
    tenant.refresh_from_db()
    assert tenant.plan.slug == "basic"
    assert has_feature("payments", tenant) is False


# ---------------------------------------------------------------- features --


def test_the_toggle_returns_every_dependent(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.post(
        url("api_tenant_feature", tenant.pk),
        data={"feature_key": "payments", "state": FeatureState.OFF},
        content_type="application/json",
    )
    effective = response.json()["data"]["effective"]
    assert "payments" not in effective
    assert "payments.refunds" not in effective
    assert "reports.financial" not in effective


def test_a_core_toggle_is_refused_with_its_own_code(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.post(
        url("api_tenant_feature", tenant.pk),
        data={"feature_key": "students", "state": FeatureState.OFF},
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ERR_CORE_FEATURE"


def test_an_unknown_state_is_refused(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.post(
        url("api_tenant_feature", tenant.pk),
        data={"feature_key": "payments", "state": "MAYBE"},
        content_type="application/json",
    )
    assert response.status_code == 400
    assert has_feature("payments", tenant) is True


# -------------------------------------------------------------- slug check --


@pytest.mark.parametrize(
    ("slug", "available"),
    [
        ("elnour", True),
        ("admin", False),  # reserved
        ("Bad Slug", False),  # not a DNS label
        ("-lead", False),
    ],
)
def test_slug_availability(console, slug, available):
    payload = console.get(url("api_slug_check") + f"?slug={slug}").json()["data"]
    assert payload.get("available", False) is available


def test_a_taken_slug_is_reported_before_submit(console):
    make_tenant("elnour", plan=make_plan("full"), host="elnour.testserver")
    payload = console.get(url("api_slug_check") + "?slug=elnour").json()["data"]
    assert payload["available"] is False
    assert payload["reason"]


def test_an_available_slug_shows_the_host_it_would_get(console):
    payload = console.get(url("api_slug_check") + "?slug=newcenter").json()["data"]
    assert payload["available"] is True
    assert payload["host"] == "newcenter.testserver"


# ------------------------------------------------------------------- usage --


def test_refreshing_usage_returns_rows_the_table_can_draw(console):
    tenant = make_tenant("alpha", plan=make_plan("basic", max_students=50))
    payload = console.post(url("api_tenant_usage", tenant.pk)).json()["data"]

    assert payload["usage"]["computed_at"]
    students = next(r for r in payload["usage"]["rows"] if r["resource"] == "students")
    assert students["limit"] == 50
    assert students["used"] == 0
    assert students["label"]


def test_usage_survives_a_client_that_was_never_counted(console):
    """A brand-new client has no usage row; the detail page still has to render."""
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.get(url("tenant_detail", tenant.pk))
    assert response.status_code == 200
