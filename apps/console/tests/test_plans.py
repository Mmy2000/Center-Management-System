"""Plans as editable, priced things (console) — and QR as a switchable method.

Two changes that meet in one place: a plan now says what it *costs*, and what it
grants includes which way a center takes attendance. A plan that grants no
method would produce a center that cannot record anything, so the form refuses
it and the toggle refuses to remove the last one.
"""

from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.console import services
from apps.tenancy.constants import BillingCycle, FeatureState, PlatformAction
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Plan, PlanFeature, PlatformAuditLog
from apps.tenancy.resolver import has_feature, remaining_methods
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
def console():
    User.objects.create_user(
        username="operator", password=PASSWORD, is_platform_staff=True, full_name="Op"
    )
    client = Client()
    client.defaults["HTTP_HOST"] = CONSOLE_HOST
    assert (
        client.post(url("login"), {"username": "operator", "password": PASSWORD}).status_code == 302
    )
    return client


def plan_payload(**overrides):
    data = {
        "name": "الباقة الذهبية",
        "slug": "gold",
        "description": "",
        "is_active": "on",
        "is_public": "on",
        "sort_order": 10,
        "monthly_price": "500.00",
        "yearly_price": "5000.00",
        "currency": "EGP",
        "discount_percent": "0",
        "discount_label": "",
        "discount_until": "",
        "max_students": 300,
        "max_users": 5,
        "max_groups": "",
        "max_cards": "",
        "storage_mb": "",
        "retention_days": 90,
        "price_note": "",
        "features": ["students", "academics", "attendance.scan", "attendance.qr", "payments"],
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# Pricing arithmetic
# --------------------------------------------------------------------------- #


def test_a_plan_with_no_discount_charges_its_price():
    plan = make_plan("p", monthly_price=Decimal("500.00"), yearly_price=Decimal("5000.00"))
    assert plan.effective_monthly == Decimal("500.00")
    assert plan.effective_yearly == Decimal("5000.00")


def test_a_live_discount_comes_off_both_prices():
    plan = make_plan(
        "p",
        monthly_price=Decimal("500.00"),
        yearly_price=Decimal("5000.00"),
        discount_percent=Decimal("10.00"),
        discount_label="افتتاح",
    )
    assert plan.effective_monthly == Decimal("450.00")
    assert plan.effective_yearly == Decimal("4500.00")


def test_an_expired_discount_is_history_not_a_price():
    """A discount whose end date has passed must stop applying on its own —
    nobody is going to remember to clear it."""
    plan = make_plan(
        "p",
        monthly_price=Decimal("500.00"),
        discount_percent=Decimal("50.00"),
        discount_until=timezone.localdate() - timezone.timedelta(days=1),
    )
    assert plan.discount_is_live is False
    assert plan.effective_monthly == Decimal("500.00")


def test_a_discount_ending_today_still_applies():
    plan = make_plan(
        "p",
        monthly_price=Decimal("500.00"),
        discount_percent=Decimal("50.00"),
        discount_until=timezone.localdate(),
    )
    assert plan.discount_is_live is True
    assert plan.effective_monthly == Decimal("250.00")


def test_a_discount_with_no_end_date_is_permanent():
    plan = make_plan("p", monthly_price=Decimal("100.00"), discount_percent=Decimal("25.00"))
    assert plan.discount_is_live is True
    assert plan.effective_monthly == Decimal("75.00")


def test_the_yearly_saving_is_computed_not_typed():
    """Two prices and a third number meant to agree with them is a number that
    will eventually disagree."""
    plan = make_plan("p", monthly_price=Decimal("500.00"), yearly_price=Decimal("5000.00"))
    assert plan.yearly_saving_percent == 17  # 6000 -> 5000

    plan.yearly_price = Decimal("6000.00")
    assert plan.yearly_saving_percent == 0


def test_a_plan_priced_on_one_cycle_only():
    plan = make_plan("p", monthly_price=Decimal("500.00"), yearly_price=None)
    assert plan.effective_yearly is None
    assert plan.yearly_saving_percent is None
    assert plan.price_for(BillingCycle.YEARLY) is None
    assert plan.price_for(BillingCycle.MONTHLY) == Decimal("500.00")


def test_a_free_plan_has_no_price():
    plan = make_plan("free")
    assert plan.price_for(BillingCycle.MONTHLY) is None


def test_the_client_pays_the_price_for_its_own_cycle():
    plan = make_plan("p", monthly_price=Decimal("500.00"), yearly_price=Decimal("5000.00"))
    monthly = make_tenant("m", plan=plan, billing_cycle=BillingCycle.MONTHLY)
    yearly = make_tenant("y", plan=plan, billing_cycle=BillingCycle.YEARLY, host="y.testserver")
    free = make_tenant("f", plan=plan, billing_cycle=BillingCycle.NONE, host="f.testserver")

    assert monthly.price == Decimal("500.00")
    assert yearly.price == Decimal("5000.00")
    assert free.price is None


# --------------------------------------------------------------------------- #
# Editing a plan from the console
# --------------------------------------------------------------------------- #


def test_creating_a_plan(console):
    response = console.post(url("plan_new"), plan_payload())
    assert response.status_code == 302

    plan = Plan.objects.get(slug="gold")
    assert plan.monthly_price == Decimal("500.00")
    assert plan.yearly_price == Decimal("5000.00")
    assert plan.is_active is True
    assert "payments" in plan.feature_keys


def test_creating_a_plan_is_audited(console):
    console.post(url("plan_new"), plan_payload())
    entry = PlatformAuditLog.objects.filter(action=PlatformAction.PLAN_CREATED).first()
    assert entry is not None
    assert entry.object_repr == "الباقة الذهبية"


def test_editing_a_plan_records_only_what_changed(console):
    plan = make_plan("gold", monthly_price=Decimal("500.00"))
    console.post(
        url("plan_edit", plan.pk),
        plan_payload(slug="gold", monthly_price="750.00", name=plan.name),
    )
    plan.refresh_from_db()
    assert plan.monthly_price == Decimal("750.00")

    entry = PlatformAuditLog.objects.filter(action=PlatformAction.PLAN_UPDATED).first()
    assert entry.changes["monthly_price"] == ["500.00", "750.00"]


def test_the_slug_cannot_be_renamed(console):
    """Scripts and `seed_plans` key off the slug; renaming would orphan them."""
    plan = make_plan("gold")
    console.post(url("plan_edit", plan.pk), plan_payload(slug="renamed", name=plan.name))
    plan.refresh_from_db()
    assert plan.slug == "gold"


def test_a_discount_needs_a_reason(console):
    """A percentage with no explanation is a number nobody can defend in six
    months."""
    response = console.post(url("plan_new"), plan_payload(discount_percent="15", discount_label=""))
    assert response.status_code == 200
    assert not Plan.objects.filter(slug="gold").exists()


def test_a_discount_over_100_is_refused(console):
    response = console.post(
        url("plan_new"), plan_payload(discount_percent="150", discount_label="خطأ")
    )
    assert response.status_code == 200
    assert not Plan.objects.filter(slug="gold").exists()


def test_editing_features_reaches_the_clients_on_the_plan(console):
    """A plan's grant list is what its clients get — changing it must bite on
    their next request, not after a cache expires."""
    plan = make_plan("gold")
    tenant = make_tenant("alpha", plan=plan)
    assert has_feature("payments", tenant) is True

    features = [f for f in plan_payload()["features"] if f != "payments"]
    console.post(url("plan_edit", plan.pk), plan_payload(slug="gold", features=features))

    assert has_feature("payments", tenant) is False


def test_core_features_are_added_whatever_is_ticked(console):
    """A plan cannot accidentally withhold what the product cannot run without."""
    console.post(
        url("plan_new"),
        plan_payload(features=["attendance.qr", "payments"]),
    )
    plan = Plan.objects.get(slug="gold")
    assert {"students", "academics", "attendance.scan"} <= plan.feature_keys


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


def test_deactivating_hides_a_plan_from_the_wizard(console):
    gold = make_plan("gold")
    make_plan("silver")

    services.set_plan_active(gold, False)

    from apps.console.forms import TenantCreateForm

    offered = {plan.slug for plan in TenantCreateForm().fields["plan"].queryset}
    assert offered == {"silver"}


def test_deactivating_leaves_existing_clients_alone(console):
    """Changing what somebody already pays for is a conversation, not a side
    effect of tidying a price list."""
    gold = make_plan("gold")
    make_plan("silver")
    tenant = make_tenant("alpha", plan=gold)

    console.post(
        url("api_plan_active", gold.pk),
        data={"is_active": False},
        content_type="application/json",
    )

    tenant.refresh_from_db()
    assert tenant.plan_id == gold.pk
    assert tenant.is_operational is True
    assert has_feature("payments", tenant) is True


def test_the_last_available_plan_cannot_be_retired(console):
    """Retiring it would leave the wizard with nothing to offer."""
    only = make_plan("gold")
    response = console.post(
        url("api_plan_active", only.pk),
        data={"is_active": False},
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ERR_LAST_PLAN"
    only.refresh_from_db()
    assert only.is_active is True


def test_reactivating_a_plan(console):
    gold = make_plan("gold", is_active=False)
    make_plan("silver")
    response = console.post(
        url("api_plan_active", gold.pk),
        data={"is_active": True},
        content_type="application/json",
    )
    assert response.json()["data"]["plan"]["is_active"] is True


def test_the_plan_list_renders_prices(console):
    make_plan(
        "gold",
        monthly_price=Decimal("500.00"),
        yearly_price=Decimal("5000.00"),
        discount_percent=Decimal("10.00"),
        discount_label="افتتاح",
    )
    html = console.get(url("plan_list")).content.decode("utf-8")
    assert "450.00" in html  # the discounted monthly
    assert "افتتاح" in html
    assert "17%" in html  # the yearly saving


# --------------------------------------------------------------------------- #
# QR as a switchable method
# --------------------------------------------------------------------------- #


def test_qr_is_on_by_default():
    """The default way in, and the only card method built today."""
    tenant = make_tenant("alpha", plan=make_plan("full"))
    assert has_feature("attendance.qr", tenant) is True


def test_fingerprint_is_reserved_and_off():
    tenant = make_tenant("alpha", plan=make_plan("full"))
    assert has_feature("attendance.fingerprint", tenant) is False


def test_switching_qr_off_leaves_manual(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    services.set_feature(tenant, "attendance.qr", FeatureState.OFF)

    assert has_feature("attendance.qr", tenant) is False
    assert has_feature("attendance.manual", tenant) is True
    assert remaining_methods(tenant) == frozenset({"attendance.manual"})


def test_the_camera_scanner_goes_with_qr():
    tenant = make_tenant("alpha", plan=make_plan("full"))
    services.set_feature(tenant, "attendance.qr", FeatureState.OFF)
    assert has_feature("attendance.camera_scanner", tenant) is False


def test_the_last_method_cannot_be_switched_off():
    """A center that can record attendance by no means at all is not a running
    center — the scanner has nowhere to go and the roll can never be filled."""
    from apps.core.http import DomainError

    tenant = make_tenant("alpha", plan=make_plan("full"))
    services.set_feature(tenant, "attendance.manual", FeatureState.OFF)

    with pytest.raises(DomainError) as exc:
        services.set_feature(tenant, "attendance.qr", FeatureState.OFF)
    assert exc.value.code == "ERR_LAST_METHOD"
    assert has_feature("attendance.qr", tenant) is True


def test_the_toggle_refuses_over_the_api(console):
    tenant = make_tenant("alpha", plan=make_plan("full"))
    services.set_feature(tenant, "attendance.manual", FeatureState.OFF)

    response = console.post(
        url("api_tenant_feature", tenant.pk),
        data={"feature_key": "attendance.qr", "state": FeatureState.OFF},
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ERR_LAST_METHOD"


def test_fingerprint_does_not_count_as_a_method_yet():
    """It is in the catalogue so an operator can see what is coming, but
    switching it on must not let QR be removed from a center that would then
    have no working way to take attendance."""
    from apps.core.http import DomainError

    tenant = make_tenant("alpha", plan=make_plan("full"))
    services.set_feature(tenant, "attendance.manual", FeatureState.OFF)
    services.set_feature(tenant, "attendance.fingerprint", FeatureState.ON)

    with pytest.raises(DomainError):
        services.set_feature(tenant, "attendance.qr", FeatureState.OFF)


def test_a_plan_must_grant_a_method(console):
    response = console.post(
        url("plan_new"),
        plan_payload(features=["students", "academics", "attendance.scan", "payments"]),
    )
    assert response.status_code == 200
    assert not Plan.objects.filter(slug="gold").exists()


# --------------------------------------------------------------------------- #
# The QR surface inside a center
# --------------------------------------------------------------------------- #


@pytest.fixture
def center():
    """A center with an owner signed in, on its own host."""
    from django.core.management import call_command

    from apps.accounts.models import Role
    from apps.accounts.services import sync_user_group

    tenant = make_tenant("alpha", plan=make_plan("full"), host="alpha.testserver")
    with tenant_context(tenant):
        call_command("seed_roles", verbosity=0)
        user = User.objects.create_user(username="boss", password=PASSWORD, role=Role.SUPER_ADMIN)
        sync_user_group(user)

    client = Client()
    client.defaults["HTTP_HOST"] = "alpha.testserver"
    with tenant_context(tenant):
        assert client.login(username="boss", password=PASSWORD)
    return tenant, client


def test_the_scanner_is_reachable_while_qr_is_on(center):
    _tenant, client = center
    assert client.get(reverse("attendance:scanner_picker")).status_code == 200


def test_the_scanner_disappears_with_qr(center):
    tenant, client = center
    services.set_feature(tenant, "attendance.qr", FeatureState.OFF)
    assert client.get(reverse("attendance:scanner_picker")).status_code == 404


def test_the_scan_endpoint_refuses_without_qr(center):
    tenant, client = center
    services.set_feature(tenant, "attendance.qr", FeatureState.OFF)
    response = client.post(
        reverse("attendance_api:scan"),
        data={"lesson_id": 1, "qr_token": "x"},
        content_type="application/json",
    )
    assert response.status_code == 404
    assert response.json()["code"] == "ERR_FEATURE_DISABLED"


def test_the_sidebar_drops_the_scan_link(center):
    tenant, client = center
    scan = reverse("attendance:scanner_picker")
    assert scan in client.get(reverse("dashboard:home")).content.decode("utf-8")

    services.set_feature(tenant, "attendance.qr", FeatureState.OFF)
    assert scan not in client.get(reverse("dashboard:home")).content.decode("utf-8")


def test_the_scan_service_refuses_from_a_shell(center):
    """The offline queue replays scans from a worker; no view is involved."""
    from apps.attendance.services import scan
    from apps.core.http import DomainError

    tenant, _client = center
    services.set_feature(tenant, "attendance.qr", FeatureState.OFF)

    with tenant_context(tenant), pytest.raises(DomainError) as exc:
        scan(lesson_id=1, qr_token="anything")
    assert exc.value.code == "ERR_FEATURE_DISABLED"


def test_switching_qr_off_destroys_no_cards(center):
    """The same promise every feature makes: the switch hides, it never
    deletes."""
    from apps.cards.models import CardStatus, StudentCard

    tenant, _client = center
    with tenant_context(tenant):
        StudentCard.objects.create(card_number="C1", qr_token="t1", status=CardStatus.AVAILABLE)
        before = StudentCard.objects.count()

    services.set_feature(tenant, "attendance.qr", FeatureState.OFF)
    assert StudentCard.all_tenants.filter(tenant=tenant).count() == before

    services.set_feature(tenant, "attendance.qr", FeatureState.INHERIT)
    assert StudentCard.all_tenants.filter(tenant=tenant).count() == before


def test_seed_plans_never_overwrites_a_price():
    """A deploy that silently reset every client's agreed price would be a very
    bad afternoon."""
    from django.core.management import call_command

    call_command("seed_plans", verbosity=0)
    basic = Plan.objects.get(slug="basic")
    assert basic.monthly_price is not None

    basic.monthly_price = Decimal("777.00")
    basic.discount_percent = Decimal("20.00")
    basic.save()

    call_command("seed_plans", verbosity=0)
    basic.refresh_from_db()
    assert basic.monthly_price == Decimal("777.00")
    assert basic.discount_percent == Decimal("20.00")


def test_seed_plans_fills_a_missing_price_once():
    """A plan created before pricing existed has no figure to protect — an
    absence is not an operator's decision."""
    from django.core.management import call_command

    legacy = Plan.objects.create(slug="basic", name="قديمة")
    assert legacy.monthly_price is None

    call_command("seed_plans", verbosity=0)
    legacy.refresh_from_db()
    assert legacy.monthly_price == Decimal("500.00")


def test_seed_plans_still_refreshes_what_it_owns():
    from django.core.management import call_command

    call_command("seed_plans", verbosity=0)
    basic = Plan.objects.get(slug="basic")
    basic.max_students = 1
    basic.save()
    PlanFeature.objects.filter(plan=basic).delete()

    call_command("seed_plans", verbosity=0)
    basic.refresh_from_db()
    assert basic.max_students == 300
    assert basic.feature_keys
