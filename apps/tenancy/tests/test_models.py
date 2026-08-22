"""TASK-089/090 — the control-plane invariants."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.tenancy.constants import RESERVED_SLUGS, TenantStatus
from apps.tenancy.models import Domain, PlanFeature, Tenant, TenantFeature

from .factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Tenant
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("slug", ["el-nour", "center1", "a1b", "x" * 32])
def test_valid_slugs(slug):
    tenant = Tenant(slug=slug, name="X", plan=make_plan())
    tenant.full_clean(exclude=["plan"])


@pytest.mark.parametrize(
    "slug",
    [
        "",
        "a",  # too short to have a middle
        "-lead",
        "trail-",
        "Upper",
        "has_underscore",
        "has space",
        "x" * 33,
    ],
)
def test_invalid_slugs_rejected(slug):
    tenant = Tenant(slug=slug, name="X", plan=make_plan())
    with pytest.raises(ValidationError):
        tenant.full_clean(exclude=["plan"])


@pytest.mark.parametrize("slug", sorted(RESERVED_SLUGS)[:6])
def test_reserved_slugs_rejected(slug):
    """A center taking `admin` or `www` would shadow the platform itself."""
    tenant = Tenant(slug=slug, name="X", plan=make_plan())
    with pytest.raises(ValidationError):
        tenant.full_clean(exclude=["plan"])


def test_slug_is_immutable_after_creation():
    """It is the DNS name: changing it breaks the certificate and every bookmark."""
    tenant = make_tenant("alpha")
    tenant.slug = "renamed"
    with pytest.raises(ValidationError):
        tenant.save()


def test_slug_unchanged_save_is_allowed():
    tenant = make_tenant("alpha")
    tenant.name = "اسم جديد"
    tenant.save()
    tenant.refresh_from_db()
    assert tenant.name == "اسم جديد"


@pytest.mark.parametrize(
    ("status", "operational"),
    [
        (TenantStatus.TRIAL, True),
        (TenantStatus.ACTIVE, True),
        # A late invoice is a conversation, not a reason to stop the morning's
        # attendance — PAST_DUE deliberately still works.
        (TenantStatus.PAST_DUE, True),
        (TenantStatus.SUSPENDED, False),
        (TenantStatus.ARCHIVED, False),
    ],
)
def test_is_operational(status, operational):
    assert make_tenant(f"t-{status.lower()}", status=status).is_operational is operational


def test_days_until_expiry():
    tenant = make_tenant("alpha", expires_at=timezone.now() + timezone.timedelta(days=10))
    assert tenant.days_until_expiry() == 9  # now+10d is 9 whole days away
    assert make_tenant("beta").days_until_expiry() is None


# --------------------------------------------------------------------------- #
# Domain
# --------------------------------------------------------------------------- #


def test_one_primary_domain_per_tenant():
    tenant = make_tenant("alpha")
    with pytest.raises(IntegrityError), transaction.atomic():
        Domain.objects.create(tenant=tenant, host="second.testserver", is_primary=True)


def test_secondary_domains_are_unlimited():
    tenant = make_tenant("alpha")
    Domain.objects.create(tenant=tenant, host="a.example.com", is_custom=True)
    Domain.objects.create(tenant=tenant, host="b.example.com", is_custom=True)
    assert tenant.domains.count() == 3


def test_host_is_normalised_on_save():
    """A Host header arrives in whatever case the client sent it."""
    tenant = make_tenant("alpha")
    domain = Domain.objects.create(tenant=tenant, host="  PORTAL.Example.COM.  ")
    assert domain.host == "portal.example.com"


def test_host_is_globally_unique():
    make_tenant("alpha", host="shared.testserver")
    with pytest.raises(IntegrityError), transaction.atomic():
        make_tenant("beta", host="shared.testserver")


def test_primary_host_property():
    tenant = make_tenant("alpha", host="alpha.testserver")
    assert tenant.primary_host == "alpha.testserver"


# --------------------------------------------------------------------------- #
# Plans & features
# --------------------------------------------------------------------------- #


def test_plan_feature_key_must_exist():
    plan = make_plan("basic", features=[])
    bad = PlanFeature(plan=plan, feature_key="payments.teleport")
    with pytest.raises(ValidationError):
        bad.full_clean()


def test_tenant_feature_key_must_exist():
    tenant = make_tenant("alpha")
    bad = TenantFeature(tenant=tenant, feature_key="nope")
    with pytest.raises(ValidationError):
        bad.full_clean()


def test_plan_feature_is_unique():
    plan = make_plan("basic", features=["payments"])
    with pytest.raises(IntegrityError), transaction.atomic():
        PlanFeature.objects.create(plan=plan, feature_key="payments")


def test_tenant_feature_is_unique_per_key():
    tenant = make_tenant("alpha")
    TenantFeature.objects.create(tenant=tenant, feature_key="payments")
    with pytest.raises(IntegrityError), transaction.atomic():
        TenantFeature.objects.create(tenant=tenant, feature_key="payments")


def test_plan_limit_lookup():
    plan = make_plan("basic", features=[], max_students=300)
    assert plan.limit("students") == 300
    assert plan.limit("users") is None


def test_seed_plans_is_idempotent():
    from django.core.management import call_command

    call_command("seed_plans", verbosity=0)
    from apps.tenancy.models import Plan

    first = {p.slug: p.feature_keys for p in Plan.objects.all()}
    call_command("seed_plans", verbosity=0)
    second = {p.slug: p.feature_keys for p in Plan.objects.all()}
    assert first == second
    assert set(first) == {"basic", "standard", "full"}


def test_seed_plans_full_has_every_feature():
    from django.core.management import call_command

    from apps.tenancy.features import FEATURE_KEYS
    from apps.tenancy.models import Plan

    call_command("seed_plans", verbosity=0)
    assert Plan.objects.get(slug="full").feature_keys == FEATURE_KEYS
