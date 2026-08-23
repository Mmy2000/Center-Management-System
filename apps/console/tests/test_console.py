"""TASK-111 → 118 — the platform console.

The first section is the one that matters most: the console must be
*structurally* unreachable from a client's site, not merely permission-guarded.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.console import services
from apps.tenancy.constants import FeatureState, PlatformAction, TenantStatus
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Plan, PlatformAuditLog, Tenant, TenantFeature
from apps.tenancy.resolver import has_feature
from apps.tenancy.tests.factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db

CONSOLE_HOST = "console.testserver"
PASSWORD = "TestPass!2026"


@pytest.fixture(autouse=True)
def console_hosts(settings):
    """The console's own hostname and the suffix clients hang off.

    An autouse fixture rather than an ``override_settings`` decorator: a
    decorator's override is only active inside the test *body*, and the
    ``console`` fixture below signs in during setup — before it would apply.
    """
    settings.CONSOLE_HOST = CONSOLE_HOST
    settings.TENANT_BASE_DOMAIN = "testserver"


@pytest.fixture
def operator():
    return User.objects.create_user(
        username="operator",
        password=PASSWORD,
        is_platform_staff=True,
        full_name="مشغّل المنصة",
    )


@pytest.fixture
def console(operator):
    """A signed-in operator on the console host."""
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    response = client.post(
        reverse("console:login", urlconf="cms.urls_console"),
        {"username": "operator", "password": PASSWORD},
    )
    assert response.status_code == 302, "operator could not sign in"
    return client


def url(name, *args):
    return reverse(f"console:{name}", args=args, urlconf="cms.urls_console")


# --------------------------------------------------------------------------- #
# TASK-111 — the console is unreachable from a client's site
# --------------------------------------------------------------------------- #


def test_a_tenant_host_has_no_route_to_the_console(operator):
    """Not "permission denied" — *no such URL*. The two URLconfs are disjoint,
    which is what makes this immune to a permission bug."""
    make_tenant("alpha", host="alpha.testserver")
    client = Client()
    client.defaults["HTTP_HOST"] = "alpha.testserver"
    assert client.get("/tenants/").status_code == 404


def test_the_tenant_urlconf_contains_no_console_route():
    from django.urls import get_resolver

    names = set(get_resolver("cms.urls").reverse_dict.keys())
    assert not any(str(name).startswith("console:") for name in names)


def test_a_center_admin_cannot_sign_in_to_the_console():
    """Same password, wrong side of the wall: the backend scopes the lookup to
    users with no tenant, so a center's own admin does not exist here."""
    tenant = make_tenant("alpha", host="alpha.testserver")
    with tenant_context(tenant):
        User.objects.create_user(username="boss", password=PASSWORD, role=Role.SUPER_ADMIN)

    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    response = client.post(url("login"), {"username": "boss", "password": PASSWORD})
    assert response.status_code == 401


def test_a_tenant_user_on_the_console_host_is_logged_out(operator):
    """The session guard refuses a tenant user here even if one arrives."""
    tenant = make_tenant("alpha", host="alpha.testserver")
    with tenant_context(tenant):
        user = User.objects.create_user(username="boss", password=PASSWORD)

    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    client.force_login(user)
    response = client.get(url("tenant_list"))
    assert response.status_code in (302, 404)


def test_a_non_staff_platform_user_cannot_reach_the_console(operator):
    """The session guard logs them out before the view is even consulted, which
    is a stronger answer than a 404: they do not stay signed in here at all."""
    plain = User.objects.create_user(username="plain", password=PASSWORD)
    assert plain.is_platform_staff is False
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    client.force_login(plain)

    response = client.get(url("tenant_list"))
    assert response.status_code in (302, 404)
    if response.status_code == 302:
        assert "/login/" in response["Location"]
    assert "_auth_user_id" not in client.session


def test_signed_out_is_redirected_to_the_console_login():
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    response = client.get(url("tenant_list"))
    assert response.status_code == 302
    assert "/login/" in response["Location"]


# --------------------------------------------------------------------------- #
# TASK-112/117 — list, detail, dashboard
# --------------------------------------------------------------------------- #


def test_the_dashboard_renders_with_no_clients(console):
    assert console.get(url("dashboard")).status_code == 200


def test_the_dashboard_counts_clients_by_status(console):
    """The page is a shell; the figures come from the API it fetches, so that
    is where the assertion belongs."""
    make_tenant("alpha", host="alpha.testserver", status=TenantStatus.ACTIVE)
    make_tenant("beta", host="beta.testserver", status=TenantStatus.SUSPENDED)

    assert console.get(url("dashboard")).status_code == 200

    data = console.get(url("api_overview")).json()["data"]
    counts = {row["value"]: row["count"] for row in data["statuses"]}
    assert counts["ACTIVE"] == 1
    assert counts["SUSPENDED"] == 1
    assert data["total"] == 2


def test_the_list_does_not_grow_queries_with_clients(console, django_assert_max_num_queries):
    plan = make_plan("full")
    for index in range(20):
        make_tenant(f"center-{index}", plan=plan, host=f"center-{index}.testserver")
    # Session, user, count, page, plans — the per-client counts come from
    # TenantUsage precisely so this does not become 20 x 6 aggregates.
    with django_assert_max_num_queries(12):
        assert console.get(url("tenant_list")).status_code == 200


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?q=alpha", {"alpha"}),
        ("?status=SUSPENDED", {"beta"}),
        ("?q=alpha.testserver", {"alpha"}),
        ("?q=nobody", set()),
        ("", {"alpha", "beta"}),
    ],
)
def test_the_list_filters(console, query, expected):
    make_tenant("alpha", host="alpha.testserver", status=TenantStatus.ACTIVE)
    make_tenant("beta", host="beta.testserver", status=TenantStatus.SUSPENDED)

    payload = console.get(url("api_tenants") + query).json()["data"]
    assert {row["slug"] for row in payload["results"]} == expected
    assert payload["count"] == len(expected)


def test_the_detail_page_shows_usage_against_limits(console):
    tenant = make_tenant("alpha", plan=make_plan("basic", max_students=300))
    html = console.get(url("tenant_detail", tenant.pk)).content.decode("utf-8")
    assert "300" in html


def test_refreshing_usage_counts_for_real(console):
    from apps.academics.models import EducationalStage, Grade
    from apps.students.services import create_student

    tenant = make_tenant("alpha")
    with tenant_context(tenant):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        create_student(full_name="طالب", grade=grade)

    console.post(url("tenant_usage_refresh", tenant.pk))
    tenant.refresh_from_db()
    assert tenant.usage.students == 1
    assert tenant.usage.computed_at is not None


# --------------------------------------------------------------------------- #
# TASK-113 — provisioning
# --------------------------------------------------------------------------- #


def test_the_wizard_creates_a_working_client(console):
    make_plan("full")
    response = console.post(
        url("tenant_new"),
        {
            "name": "سنتر النور",
            "slug": "elnour",
            "plan": Plan.objects.get(slug="full").pk,
            "trial_days": 30,
            "billing_cycle": "MONTHLY",
            "owner_username": "admin",
            "owner_name": "أحمد",
            "owner_email": "a@example.com",
            "owner_phone": "01000000000",
            "seed_academics": "on",
        },
    )
    assert response.status_code == 200
    tenant = Tenant.objects.get(slug="elnour")
    assert tenant.primary_host == "elnour.testserver"
    assert tenant.status == TenantStatus.TRIAL

    # The one-time password is on screen, and it works.
    password = response.context["password"]
    assert len(password) >= 12

    site = Client()
    site.defaults["HTTP_HOST"] = "elnour.testserver"
    with tenant_context(tenant):
        assert site.login(username="admin", password=password) is True
        owner = User.objects.get(username="admin")
    assert owner.must_change_password is True
    assert owner.role == Role.CENTER_ADMIN


def test_provisioning_is_all_or_nothing(console, monkeypatch):
    """A half-built tenant is worse than none: it holds the slug, answers on the
    subdomain, and has no way in."""
    make_plan("full")
    monkeypatch.setattr(
        services, "make_password", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        services.provision_tenant(
            slug="doomed",
            name="Doomed",
            plan=Plan.objects.get(slug="full"),
            base_domain="testserver",
        )
    assert not Tenant.objects.filter(slug="doomed").exists()


def test_a_duplicate_slug_is_refused(console):
    make_tenant("elnour", plan=make_plan("full"), host="elnour.testserver")
    response = console.post(
        url("tenant_new"),
        {
            "name": "Another",
            "slug": "elnour",
            "plan": Plan.objects.get(slug="full").pk,
            "trial_days": 30,
            "owner_username": "admin",
        },
    )
    assert response.status_code == 200
    assert Tenant.objects.filter(slug="elnour").count() == 1


def test_a_reserved_slug_is_refused(console):
    make_plan("full")
    response = console.post(
        url("tenant_new"),
        {
            "name": "X",
            "slug": "admin",
            "plan": Plan.objects.get(slug="full").pk,
            "trial_days": 30,
            "owner_username": "admin",
        },
    )
    assert response.status_code == 200
    assert not Tenant.objects.filter(slug="admin").exists()


def test_the_command_and_the_wizard_do_the_same_thing():
    from django.core.management import call_command

    make_plan("full")
    call_command(
        "provision_tenant",
        "--slug",
        "cli",
        "--name",
        "CLI Center",
        "--base-domain",
        "testserver",
        verbosity=0,
    )
    tenant = Tenant.objects.get(slug="cli")
    assert tenant.primary_host == "cli.testserver"
    with tenant_context(tenant):
        assert User.objects.filter(username="admin").exists()


# --------------------------------------------------------------------------- #
# TASK-114 — lifecycle, plan, features
# --------------------------------------------------------------------------- #


def test_suspending_needs_a_reason(console):
    tenant = make_tenant("alpha")
    console.post(url("tenant_status", tenant.pk), {"action": "suspend", "reason": ""})
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.ACTIVE


def test_suspend_and_resume(console):
    tenant = make_tenant("alpha")
    console.post(url("tenant_status", tenant.pk), {"action": "suspend", "reason": "لم يتم السداد"})
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.SUSPENDED

    console.post(url("tenant_status", tenant.pk), {"action": "resume", "reason": "تم السداد"})
    tenant.refresh_from_db()
    assert tenant.is_operational is True


def test_toggling_a_feature_takes_effect_and_is_audited(console, operator):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    assert has_feature("payments", tenant) is True

    response = console.post(
        url("tenant_feature_set", tenant.pk),
        {"feature_key": "payments", "state": FeatureState.OFF},
    )
    assert response.status_code == 200
    assert has_feature("payments", tenant) is False

    entry = PlatformAuditLog.objects.filter(
        tenant=tenant, action=PlatformAction.FEATURE_CHANGED
    ).first()
    assert entry.actor_id == operator.pk
    assert entry.changes["payments"] == ["INHERIT", "OFF"]


def test_the_toggle_response_carries_every_dependent(console):
    """One switch can flip several rows; the screen has to be told all of them."""
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.post(
        url("tenant_feature_set", tenant.pk),
        {"feature_key": "payments", "state": FeatureState.OFF},
    )
    effective = response.json()["data"]["effective"]
    assert "payments" not in effective
    assert "payments.refunds" not in effective
    assert "reports.financial" not in effective


def test_a_core_feature_cannot_be_switched_off(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.post(
        url("tenant_feature_set", tenant.pk),
        {"feature_key": "students", "state": FeatureState.OFF},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ERR_CORE_FEATURE"
    assert has_feature("students", tenant) is True


def test_setting_inherit_deletes_the_row(console):
    """Storing "INHERIT" would let the plan default and the stored state drift
    apart, and then nobody could tell which one was answering."""
    tenant = make_tenant("alpha", plan=make_plan("full"))
    console.post(
        url("tenant_feature_set", tenant.pk),
        {"feature_key": "payments", "state": FeatureState.OFF},
    )
    assert TenantFeature.objects.filter(tenant=tenant, feature_key="payments").exists()

    console.post(
        url("tenant_feature_set", tenant.pk),
        {"feature_key": "payments", "state": FeatureState.INHERIT},
    )
    assert not TenantFeature.objects.filter(tenant=tenant, feature_key="payments").exists()
    assert has_feature("payments", tenant) is True


def test_the_feature_screen_shows_plan_override_and_effective(console):
    """All three states on one row, so nobody has to hold the precedence rules
    in their head."""
    tenant = make_tenant("alpha", plan=make_plan("full"))
    response = console.get(url("tenant_features", tenant.pk))
    html = response.content.decode("utf-8")

    assert response.status_code == 200
    assert 'data-key="payments"' in html  # the row exists
    assert "data-plan" in html  # what the plan grants
    assert "data-effective" in html  # what the client actually gets
    assert 'class="form-select form-select-sm feature-state"' in html  # the override

    groups = response.context["groups"]
    row = next(r for group in groups for r in group["rows"] if r["spec"].key == "payments")
    assert row["plan_default"] is True
    assert row["effective"] is True
    assert row["state"] == "INHERIT"


def test_changing_a_plan_warns_about_what_is_lost(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    basic = make_plan("basic", features=["students", "academics", "attendance.scan"])
    response = console.post(
        url("tenant_plan", tenant.pk), {"plan": basic.pk, "reason": "downgrade"}, follow=True
    )
    body = response.content.decode("utf-8")
    assert "ستفقد" in body
    tenant.refresh_from_db()
    assert tenant.plan_id == basic.pk


def test_a_plan_change_that_breaches_a_limit_is_flagged(console):
    from apps.academics.models import EducationalStage, Grade
    from apps.students.services import create_student

    tenant = make_tenant("alpha", plan=make_plan("full"))
    with tenant_context(tenant):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        for index in range(3):
            create_student(full_name=f"طالب {index}", grade=grade)

    tiny = make_plan("tiny", max_students=1)
    response = console.post(
        url("tenant_plan", tenant.pk), {"plan": tiny.pk, "reason": ""}, follow=True
    )
    assert "الحد في هذه الباقة" in response.content.decode("utf-8")


# --------------------------------------------------------------------------- #
# TASK-115 — impersonation
# --------------------------------------------------------------------------- #


def test_entering_a_client_needs_a_reason(console):
    tenant = make_tenant("alpha", host="alpha.testserver")
    console.post(url("tenant_enter", tenant.pk), {"mode": "read", "reason": ""})
    assert "impersonation" not in console.session


def test_entering_records_the_start(console, operator):
    tenant = make_tenant("alpha", host="alpha.testserver")
    response = console.post(
        url("tenant_enter", tenant.pk), {"mode": "read", "reason": "يحتاج مساعدة"}
    )
    assert response.status_code == 302
    assert "alpha.testserver" in response["Location"]

    record = console.session["impersonation"]
    assert record["tenant_id"] == tenant.pk
    assert record["mode"] == "read"

    entry = PlatformAuditLog.objects.filter(action=PlatformAction.IMPERSONATION_STARTED).first()
    assert entry.actor_id == operator.pk
    assert entry.reason == "يحتاج مساعدة"


def test_a_suspended_client_cannot_be_entered(console):
    tenant = make_tenant("alpha", host="alpha.testserver", status=TenantStatus.SUSPENDED)
    console.post(url("tenant_enter", tenant.pk), {"mode": "read", "reason": "help"})
    assert "impersonation" not in console.session


def test_write_mode_needs_a_detailed_reason(console):
    tenant = make_tenant("alpha", host="alpha.testserver")
    console.post(url("tenant_enter", tenant.pk), {"mode": "write", "reason": "fix"})
    assert "impersonation" not in console.session


# --------------------------------------------------------------------------- #
# TASK-116 — export and deletion
# --------------------------------------------------------------------------- #


def test_the_export_covers_every_tenant_owned_model():
    """Parametrised over the registry, so a model added later is exported the
    day it is added rather than the day someone notices."""
    from django.apps import apps as django_apps

    from apps.console.export import exportable_models
    from apps.tenancy.base import TenantOwnedModel

    exported = {m._meta.label_lower for m in exportable_models()}
    owned = {
        m._meta.label_lower for m in django_apps.get_models() if issubclass(m, TenantOwnedModel)
    }
    assert owned <= exported, owned - exported
    assert "accounts.user" in exported
    assert "core.auditlog" in exported


def test_the_export_downloads_and_counts(console):
    import io
    import json
    import zipfile

    from apps.academics.models import EducationalStage, Grade
    from apps.students.services import create_student

    tenant = make_tenant("alpha")
    with tenant_context(tenant):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        create_student(full_name="طالب", grade=grade)

    response = console.get(url("tenant_export", tenant.pk))
    assert response.status_code == 200
    payload = b"".join(response.streaming_content)

    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        data = json.loads(archive.read("data.json"))

    assert manifest["tenant"]["slug"] == "alpha"
    assert manifest["counts"]["students.student"] == 1
    assert manifest["total_rows"] == len(data)


def test_the_export_carries_only_one_centers_rows(console):
    import io
    import json
    import zipfile

    from apps.academics.models import EducationalStage

    alpha = make_tenant("alpha")
    beta = make_tenant("beta")
    with tenant_context(alpha):
        EducationalStage.objects.create(name="Alpha stage", code="A", order=1)
    with tenant_context(beta):
        EducationalStage.objects.create(name="Beta stage", code="B", order=1)

    payload = b"".join(console.get(url("tenant_export", alpha.pk)).streaming_content)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        body = archive.read("data.json").decode("utf-8")
        manifest = json.loads(archive.read("manifest.json"))

    assert "Alpha stage" in body
    assert "Beta stage" not in body
    assert manifest["counts"]["academics.educationalstage"] == 1


def test_deleting_requires_typing_the_slug(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    console.post(url("tenant_delete", tenant.pk), {"confirm_slug": "wrong", "reason": "leaving"})
    tenant.refresh_from_db()
    assert tenant.status != TenantStatus.ARCHIVED


def test_deleting_archives_and_schedules_a_purge(console):
    tenant = make_tenant("alpha", plan=make_plan("full", retention_days=30))
    console.post(url("tenant_delete", tenant.pk), {"confirm_slug": "alpha", "reason": "leaving"})
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.ARCHIVED
    assert tenant.purge_after is not None


def test_purge_respects_the_retention_window(console):
    from django.core.management import call_command

    tenant = make_tenant("alpha", plan=make_plan("full", retention_days=30))
    console.post(url("tenant_delete", tenant.pk), {"confirm_slug": "alpha", "reason": "leaving"})

    call_command("purge_tenants", verbosity=0)
    assert Tenant.objects.filter(slug="alpha").exists(), "purged before its retention window"


def test_purge_removes_everything_once_due(console):
    from django.core.management import call_command
    from django.utils import timezone

    from apps.academics.models import EducationalStage, Grade
    from apps.students.models import Student
    from apps.students.services import create_student

    tenant = make_tenant("alpha", plan=make_plan("full", retention_days=1))
    other = make_tenant("beta")
    with tenant_context(tenant):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        create_student(full_name="طالب", grade=grade)
    with tenant_context(other):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        create_student(full_name="آخر", grade=grade)

    services.archive(tenant, reason="leaving")
    tenant.purge_after = timezone.now() - timezone.timedelta(days=1)
    tenant.save(update_fields=["purge_after"])

    call_command("purge_tenants", verbosity=0)

    assert not Tenant.objects.filter(slug="alpha").exists()
    assert Student.all_tenants.filter(tenant=other).count() == 1, "purged the wrong center"


def test_purge_dry_run_deletes_nothing(console):
    from django.core.management import call_command
    from django.utils import timezone

    tenant = make_tenant("alpha", plan=make_plan("full", retention_days=1))
    services.archive(tenant, reason="leaving")
    tenant.purge_after = timezone.now() - timezone.timedelta(days=1)
    tenant.save(update_fields=["purge_after"])

    call_command("purge_tenants", "--dry-run", verbosity=0)
    assert Tenant.objects.filter(slug="alpha").exists()


# --------------------------------------------------------------------------- #
# TASK-118 — audit
# --------------------------------------------------------------------------- #


def test_every_console_mutation_is_audited(console, operator):
    """A mutation nobody recorded is a mutation nobody can answer for."""
    tenant = make_tenant("alpha", plan=make_plan("full"), host="alpha.testserver")
    before = PlatformAuditLog.objects.count()

    console.post(url("tenant_status", tenant.pk), {"action": "suspend", "reason": "unpaid"})
    console.post(url("tenant_status", tenant.pk), {"action": "resume", "reason": "paid"})
    console.post(
        url("tenant_feature_set", tenant.pk),
        {"feature_key": "payments", "state": FeatureState.OFF},
    )
    console.post(url("tenant_plan", tenant.pk), {"plan": make_plan("basic").pk, "reason": "x"})

    assert PlatformAuditLog.objects.count() == before + 4


def test_a_refused_mutation_writes_no_success_row(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    before = PlatformAuditLog.objects.count()
    console.post(url("tenant_status", tenant.pk), {"action": "suspend", "reason": ""})
    assert PlatformAuditLog.objects.count() == before


def test_the_audit_view_filters(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    console.post(url("tenant_status", tenant.pk), {"action": "suspend", "reason": "unpaid"})
    html = console.get(url("audit") + f"?tenant={tenant.pk}").content.decode("utf-8")
    assert "unpaid" in html


def test_the_plans_page_renders(console):
    make_plan("full")
    assert console.get(url("plan_list")).status_code == 200


def test_health_reports_client_counts(console):
    make_tenant("alpha", status=TenantStatus.ACTIVE)
    payload = console.get(url("health")).json()
    assert payload["tenants"]["ACTIVE"] == 1
