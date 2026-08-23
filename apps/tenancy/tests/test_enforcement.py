"""TASK-108/109/110 — a disabled feature is unreachable, and data survives it.

The matrix below is the contract: for each feature, the URLs that must vanish
when it is off. It is checked in both directions — on, the URL answers; off, it
404s — because a gate that is always closed passes a one-sided test just as well
as a correct one.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.tenancy.constants import FeatureState, TenantStatus
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Domain, TenantFeature
from apps.tenancy.resolver import has_feature

from .factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db

PASSWORD = "TestPass!2026"


@pytest.fixture
def center(client):
    """One center on ``testserver``, with an owner signed in."""
    from django.core.management import call_command

    tenant = make_tenant("alpha", plan=make_plan("full"), host="alpha.testserver")
    with tenant_context(tenant):
        call_command("seed_roles", verbosity=0)
        user = User.objects.create_user(username="boss", password=PASSWORD, role=Role.SUPER_ADMIN)
        from apps.accounts.services import sync_user_group

        sync_user_group(user)
    client.defaults["HTTP_HOST"] = "alpha.testserver"
    # `client.login()` calls authenticate() directly — it never goes through the
    # middleware, so nothing has put a tenant in context. Real logins do (the
    # POST to /accounts/login/ is a request); this shortcut has to say so.
    with tenant_context(tenant):
        assert client.login(username="boss", password=PASSWORD)
    return tenant, client


def turn_off(tenant, key):
    TenantFeature.objects.update_or_create(
        tenant=tenant, feature_key=key, defaults={"state": FeatureState.OFF}
    )


#: feature -> page URLs that must disappear with it.
PAGE_MATRIX = [
    ("payments", "payments:workspace", {}),
    ("cards", "cards:list", {}),
    ("settings.editor", "core:settings", {}),
    ("audit.viewer", "core:audit_log", {}),
    ("users.management", "accounts:users", {}),
]

#: feature -> AJAX endpoints that must answer ERR_FEATURE_DISABLED with it off.
API_MATRIX = [
    ("payments", "payments_api:charges", {}),
    ("payments", "payments_api:payments", {}),
    ("cards", "cards_api:cards", {}),
    ("cards", "cards_api:available", {}),
    ("settings.editor", "core_api:settings_list", {}),
    ("users.management", "accounts_api:users", {}),
]


@pytest.mark.parametrize(("feature", "url_name", "kwargs"), PAGE_MATRIX)
def test_a_page_answers_while_its_feature_is_on(feature, url_name, kwargs, center):
    tenant, client = center
    assert has_feature(feature, tenant) is True
    assert client.get(reverse(url_name, kwargs=kwargs)).status_code == 200


@pytest.mark.parametrize(("feature", "url_name", "kwargs"), PAGE_MATRIX)
def test_a_page_404s_once_its_feature_is_off(feature, url_name, kwargs, center):
    """404, not 403. A center that did not buy payments should not learn that
    the payments page exists and is being withheld."""
    tenant, client = center
    turn_off(tenant, feature)
    assert client.get(reverse(url_name, kwargs=kwargs)).status_code == 404


@pytest.mark.parametrize(("feature", "url_name", "kwargs"), API_MATRIX)
def test_an_endpoint_answers_while_its_feature_is_on(feature, url_name, kwargs, center):
    tenant, client = center
    response = client.get(reverse(url_name, kwargs=kwargs))
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.parametrize(("feature", "url_name", "kwargs"), API_MATRIX)
def test_an_endpoint_refuses_once_its_feature_is_off(feature, url_name, kwargs, center):
    tenant, client = center
    turn_off(tenant, feature)
    response = client.get(reverse(url_name, kwargs=kwargs))
    assert response.status_code == 404
    assert response.json()["code"] == "ERR_FEATURE_DISABLED"


def test_a_dependent_endpoint_goes_with_its_parent(center):
    """Switching payments off must take the refund endpoint too, without anyone
    having to remember to switch it off separately."""
    tenant, client = center
    turn_off(tenant, "payments")
    response = client.post(reverse("payments_api:refund", kwargs={"pk": 1}))
    assert response.status_code == 404
    assert response.json()["code"] == "ERR_FEATURE_DISABLED"


# ------------------------------------------------------------------- nav --


def test_the_sidebar_hides_what_is_disabled(center):
    tenant, client = center
    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert reverse("payments:workspace") in html
    assert reverse("cards:list") in html

    turn_off(tenant, "payments")
    turn_off(tenant, "cards")

    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert reverse("payments:workspace") not in html
    assert reverse("cards:list") not in html


def test_the_language_switch_follows_its_feature(center):
    tenant, client = center
    switch = reverse("set_language")
    assert switch in client.get(reverse("dashboard:home")).content.decode("utf-8")
    turn_off(tenant, "i18n.english")
    assert switch not in client.get(reverse("dashboard:home")).content.decode("utf-8")


# --------------------------------------------------------------- reports --


def test_financial_reports_disappear_with_payments(center):
    tenant, client = center
    listing = client.get(reverse("reports_api:report_list")).json()["data"]["results"]
    assert any(r["slug"] == "outstanding" for r in listing)

    turn_off(tenant, "payments")

    listing = client.get(reverse("reports_api:report_list")).json()["data"]["results"]
    assert not any(r["slug"] == "outstanding" for r in listing)
    # ...and the slug itself is a 404, not merely hidden from the list.
    response = client.get(reverse("reports_api:report_data", kwargs={"slug": "outstanding"}))
    assert response.json()["code"] == "ERR_NOT_FOUND"


def test_attendance_reports_survive_payments_being_off(center):
    tenant, client = center
    turn_off(tenant, "payments")
    listing = client.get(reverse("reports_api:report_list")).json()["data"]["results"]
    assert any(r["group"] == "attendance" for r in listing)


# ---------------------------------------------------- services (layer 4) --


def test_a_service_refuses_even_with_no_request(center):
    """Beat and management commands never touch a view, so the service guard is
    the only thing standing between them and a disabled feature."""
    from apps.core.http import DomainError
    from apps.payments.services import generate_monthly_charges

    tenant, _client = center
    turn_off(tenant, "payments")
    with tenant_context(tenant), pytest.raises(DomainError) as exc:
        generate_monthly_charges("2026-08-01")
    assert exc.value.code == "ERR_FEATURE_DISABLED"


# ------------------------------------------------------- data is untouched --


def test_disabling_a_feature_destroys_no_data(center):
    """Turn payments off, turn it back on, and the ledger is exactly as it was.
    This is the promise that makes a feature switch safe to flip."""
    from apps.payments.models import MonthlyCharge, Payment

    tenant, client = center
    with tenant_context(tenant):
        before = (MonthlyCharge.all_tenants.count(), Payment.all_tenants.count())

    turn_off(tenant, "payments")
    assert client.get(reverse("payments:workspace")).status_code == 404
    with tenant_context(tenant):
        assert (MonthlyCharge.all_tenants.count(), Payment.all_tenants.count()) == before

    TenantFeature.objects.filter(tenant=tenant, feature_key="payments").delete()
    assert client.get(reverse("payments:workspace")).status_code == 200
    with tenant_context(tenant):
        assert (MonthlyCharge.all_tenants.count(), Payment.all_tenants.count()) == before


def test_one_centers_toggle_does_not_touch_another(center):
    tenant, client = center
    other = make_tenant("beta", plan=make_plan("full"), host="beta.testserver")
    turn_off(tenant, "payments")
    assert has_feature("payments", tenant) is False
    assert has_feature("payments", other) is True


# ---------------------------------------------------------------- quotas --


def test_a_full_plan_refuses_the_next_student(center):
    from apps.academics.models import EducationalStage, Grade
    from apps.core.http import DomainError
    from apps.students.services import create_student

    tenant, _client = center
    tenant.plan.max_students = 1
    tenant.plan.save()

    with tenant_context(tenant):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        create_student(full_name="الأول", grade=grade)
        with pytest.raises(DomainError) as exc:
            create_student(full_name="الثاني", grade=grade)

    assert exc.value.code == "ERR_QUOTA_EXCEEDED"
    assert exc.value.data["limit"] == 1


def test_an_unlimited_plan_never_refuses(center):
    from apps.academics.models import EducationalStage, Grade
    from apps.students.services import create_student

    tenant, _client = center
    assert tenant.plan.max_students is None
    with tenant_context(tenant):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        for index in range(5):
            create_student(full_name=f"طالب {index}", grade=grade)


def test_raising_a_limit_takes_effect_at_once(center):
    from apps.academics.models import EducationalStage, Grade
    from apps.core.http import DomainError
    from apps.students.services import create_student
    from apps.tenancy import quota

    tenant, _client = center
    tenant.plan.max_students = 1
    tenant.plan.save()

    with tenant_context(tenant):
        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        create_student(full_name="الأول", grade=grade)
        with pytest.raises(DomainError):
            create_student(full_name="الثاني", grade=grade)

        tenant.plan.max_students = 10
        tenant.plan.save()
        quota.invalidate(tenant)
        create_student(full_name="الثاني", grade=grade)


def test_a_bulk_import_is_all_or_nothing(center):
    """A partial import would leave the print shop's sheet and the database
    disagreeing about which card numbers exist."""
    from apps.cards.importer import generate_batch
    from apps.cards.models import StudentCard
    from apps.core.http import DomainError

    tenant, _client = center
    tenant.plan.max_cards = 10
    tenant.plan.save()

    with tenant_context(tenant):
        with pytest.raises(DomainError) as exc:
            generate_batch(50)
        assert exc.value.code == "ERR_QUOTA_EXCEEDED"
        assert StudentCard.objects.count() == 0


# ------------------------------------------------------------- lifecycle --


def test_transition_records_who_and_why(center):
    from apps.tenancy.models import PlatformAuditLog

    tenant, _client = center
    tenant.transition_to(TenantStatus.SUSPENDED, reason="لم يتم السداد")
    tenant.refresh_from_db()

    assert tenant.status == TenantStatus.SUSPENDED
    assert tenant.suspended_at is not None
    assert tenant.suspended_reason == "لم يتم السداد"
    entry = PlatformAuditLog.objects.filter(tenant=tenant).first()
    assert entry.changes["status"] == ["ACTIVE", "SUSPENDED"]
    assert entry.reason == "لم يتم السداد"


def test_resuming_clears_the_suspension(center):
    tenant, _client = center
    tenant.transition_to(TenantStatus.SUSPENDED, reason="test")
    tenant.transition_to(TenantStatus.ACTIVE, reason="paid")
    tenant.refresh_from_db()
    assert tenant.suspended_at is None
    assert tenant.suspended_reason == ""


def test_suspension_is_lossless(center):
    """A door, not a shredder."""
    from apps.students.models import Student

    tenant, client = center
    with tenant_context(tenant):
        from apps.academics.models import EducationalStage, Grade
        from apps.students.services import create_student

        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        create_student(full_name="طالب", grade=grade)
        before = Student.all_tenants.filter(tenant=tenant).count()

    tenant.transition_to(TenantStatus.SUSPENDED, reason="unpaid")
    assert client.get(reverse("dashboard:home")).status_code == 403
    assert Student.all_tenants.filter(tenant=tenant).count() == before

    tenant.transition_to(TenantStatus.ACTIVE, reason="paid")
    assert Student.all_tenants.filter(tenant=tenant).count() == before


def test_a_suspended_center_cannot_log_in(center):
    from django.test import Client

    tenant, _client = center
    tenant.transition_to(TenantStatus.SUSPENDED, reason="unpaid")
    fresh = Client()
    fresh.defaults["HTTP_HOST"] = "alpha.testserver"
    with tenant_context(tenant):
        assert fresh.login(username="boss", password=PASSWORD) is False


def test_enforce_subscriptions_walks_the_clock():
    from django.core.management import call_command
    from django.utils import timezone
    from freezegun import freeze_time

    tenant = make_tenant(
        "trialcenter",
        plan=make_plan("full"),
        status=TenantStatus.TRIAL,
        trial_ends_at=timezone.now() + timezone.timedelta(days=1),
        host="trial.testserver",
    )

    call_command("enforce_subscriptions", verbosity=0)
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.TRIAL

    with freeze_time(timezone.now() + timezone.timedelta(days=2)):
        call_command("enforce_subscriptions", "--grace-days", "7", verbosity=0)
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.PAST_DUE
    assert tenant.grace_until is not None

    # Still working: a late invoice is a conversation, not a lockout.
    assert tenant.is_operational is True

    with freeze_time(timezone.now() + timezone.timedelta(days=30)):
        call_command("enforce_subscriptions", verbosity=0)
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.SUSPENDED


def test_enforce_subscriptions_is_idempotent():
    from django.core.management import call_command
    from django.utils import timezone

    from apps.tenancy.models import PlatformAuditLog

    make_tenant(
        "lapsed",
        plan=make_plan("full"),
        status=TenantStatus.ACTIVE,
        expires_at=timezone.now() - timezone.timedelta(days=1),
        host="lapsed.testserver",
    )
    call_command("enforce_subscriptions", verbosity=0)
    first = PlatformAuditLog.objects.count()
    call_command("enforce_subscriptions", verbosity=0)
    assert PlatformAuditLog.objects.count() == first


def test_a_hand_suspended_center_is_never_auto_resumed():
    from django.core.management import call_command

    tenant = make_tenant(
        "manual", plan=make_plan("full"), status=TenantStatus.ACTIVE, host="manual.testserver"
    )
    tenant.transition_to(TenantStatus.SUSPENDED, reason="operator decision")
    call_command("enforce_subscriptions", verbosity=0)
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.SUSPENDED


def test_dry_run_writes_nothing():
    from django.core.management import call_command
    from django.utils import timezone

    tenant = make_tenant(
        "lapsed",
        plan=make_plan("full"),
        status=TenantStatus.ACTIVE,
        expires_at=timezone.now() - timezone.timedelta(days=1),
        host="lapsed.testserver",
    )
    call_command("enforce_subscriptions", "--dry-run", verbosity=0)
    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.ACTIVE


def test_the_banner_warns_before_a_trial_ends(center):
    from django.utils import timezone

    tenant, client = center
    tenant.status = TenantStatus.TRIAL
    tenant.trial_ends_at = timezone.now() + timezone.timedelta(days=3)
    tenant.save()

    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert "الفترة التجريبية" in html


def test_no_banner_while_everything_is_paid(center):
    tenant, client = center
    html = client.get(reverse("dashboard:home")).content.decode("utf-8")
    assert "الفترة التجريبية" not in html
    assert "انتهى الاشتراك" not in html


def test_a_past_due_center_is_warned_but_not_blocked(center):
    tenant, client = center
    tenant.transition_to(TenantStatus.PAST_DUE, reason="lapsed")
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 200
    assert "انتهى الاشتراك" in response.content.decode("utf-8")


def test_a_second_domain_reaches_the_same_center(center):
    tenant, client = center
    Domain.objects.create(tenant=tenant, host="portal.example.com", is_custom=True)
    from django.test import Client

    other = Client()
    other.defaults["HTTP_HOST"] = "portal.example.com"
    with tenant_context(tenant):
        assert other.login(username="boss", password=PASSWORD) is True
