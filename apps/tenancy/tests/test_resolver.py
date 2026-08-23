"""TASK-107 — resolution order, dependencies, and the cache that keeps it free."""

import pytest
from django.core.cache import cache

from apps.tenancy.constants import FeatureState
from apps.tenancy.context import tenant_context
from apps.tenancy.exceptions import UnknownFeature
from apps.tenancy.features import CORE_KEYS, FEATURE_KEYS
from apps.tenancy.models import Plan, PlanFeature, TenantFeature
from apps.tenancy.resolver import cache_key, enabled_features, has_feature

from .factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant():
    """A plan that lists every feature, so overrides are the only variable."""
    return make_tenant("alpha", plan=make_plan("full"))


# ------------------------------------------------------- the five branches --


def test_plan_grants_a_feature(tenant):
    assert has_feature("payments", tenant) is True


def test_a_feature_absent_from_the_plan_is_off():
    tenant = make_tenant("basic-center", plan=make_plan("basic", features=["students"]))
    assert has_feature("payments", tenant) is False


def test_an_override_beats_the_plan(tenant):
    TenantFeature.objects.create(tenant=tenant, feature_key="payments", state=FeatureState.OFF)
    assert has_feature("payments", tenant) is False


def test_an_override_can_grant_what_the_plan_withholds():
    plan = make_plan("basic", features=["students"])
    tenant = make_tenant("alpha", plan=plan)
    assert has_feature("cards", tenant) is False
    TenantFeature.objects.create(tenant=tenant, feature_key="cards", state=FeatureState.ON)
    assert has_feature("cards", tenant) is True


def test_inherit_falls_back_to_the_plan(tenant):
    """INHERIT must behave exactly as if the row were not there."""
    TenantFeature.objects.create(tenant=tenant, feature_key="payments", state=FeatureState.INHERIT)
    assert has_feature("payments", tenant) is True


def test_a_plan_listing_nothing_falls_back_to_the_catalogue():
    """The installation migrated from single-tenant has no plan data yet, and
    must behave as it did before — everything the catalogue defaults on."""
    tenant = make_tenant("legacy", plan=make_plan("migrated", features=[]))
    assert has_feature("payments", tenant) is True
    assert has_feature("attendance.offline_queue", tenant) is False  # default off


# ------------------------------------------------------------ dependencies --


def test_disabling_payments_takes_the_money_screens_with_it(tenant):
    TenantFeature.objects.create(tenant=tenant, feature_key="payments", state=FeatureState.OFF)
    features = enabled_features(tenant)
    for dependent in (
        "payments.refunds",
        "payments.waivers",
        "payments.receipt_pdf",
        "reports.financial",
    ):
        assert dependent not in features, dependent


def test_a_dependent_can_be_switched_off_alone(tenant):
    TenantFeature.objects.create(
        tenant=tenant, feature_key="payments.refunds", state=FeatureState.OFF
    )
    assert has_feature("payments", tenant) is True
    assert has_feature("payments.refunds", tenant) is False


def test_an_override_cannot_resurrect_an_orphaned_dependent(tenant):
    """`refunds ON` while `payments OFF` is contradictory; payments wins."""
    TenantFeature.objects.create(tenant=tenant, feature_key="payments", state=FeatureState.OFF)
    TenantFeature.objects.create(
        tenant=tenant, feature_key="payments.refunds", state=FeatureState.ON
    )
    assert has_feature("payments.refunds", tenant) is False


# -------------------------------------------------------------------- core --


@pytest.mark.parametrize("key", sorted(CORE_KEYS))
def test_core_features_cannot_be_switched_off(key, tenant):
    TenantFeature.objects.create(tenant=tenant, feature_key=key, state=FeatureState.OFF)
    assert has_feature(key, tenant) is True


def test_core_survives_a_plan_that_omits_it():
    tenant = make_tenant("alpha", plan=make_plan("bare", features=["payments"]))
    for key in CORE_KEYS:
        assert has_feature(key, tenant) is True


# ------------------------------------------------------------------ typos --


def test_an_unknown_key_raises(tenant):
    with pytest.raises(UnknownFeature):
        has_feature("payments.teleport", tenant)


# ------------------------------------------------------------------ cache --


def test_resolution_is_cached(tenant, django_assert_num_queries):
    cache.clear()
    enabled_features(tenant)  # warms it
    with django_assert_num_queries(0):
        enabled_features(tenant)
        enabled_features(tenant)


def test_a_toggle_takes_effect_on_the_next_request(tenant):
    """Five minutes of stale answers is not acceptable for a switch an operator
    just flipped, so every write invalidates rather than waiting for the TTL."""
    assert has_feature("payments", tenant) is True
    TenantFeature.objects.create(tenant=tenant, feature_key="payments", state=FeatureState.OFF)
    assert has_feature("payments", tenant) is False


def test_deleting_an_override_takes_effect_immediately(tenant):
    override = TenantFeature.objects.create(
        tenant=tenant, feature_key="payments", state=FeatureState.OFF
    )
    assert has_feature("payments", tenant) is False
    override.delete()
    assert has_feature("payments", tenant) is True


def test_changing_the_plans_features_reaches_every_tenant_on_it():
    plan = make_plan("shared")
    alpha = make_tenant("alpha", plan=plan)
    beta = make_tenant("beta", plan=plan)
    assert has_feature("payments", alpha) and has_feature("payments", beta)

    PlanFeature.objects.filter(plan=plan, feature_key="payments").delete()

    assert has_feature("payments", alpha) is False
    assert has_feature("payments", beta) is False


def test_moving_a_tenant_to_another_plan_takes_effect(tenant):
    assert has_feature("payments", tenant) is True
    tenant.plan = make_plan("basic", features=["students"])
    tenant.save()
    assert has_feature("payments", tenant) is False


def test_the_cache_key_is_per_tenant():
    alpha = make_tenant("alpha")
    beta = make_tenant("beta")
    assert cache_key(alpha.pk) != cache_key(beta.pk)


def test_two_centers_resolve_independently():
    plan = make_plan("shared")
    alpha = make_tenant("alpha", plan=plan)
    beta = make_tenant("beta", plan=plan)
    TenantFeature.objects.create(tenant=alpha, feature_key="payments", state=FeatureState.OFF)
    assert has_feature("payments", alpha) is False
    assert has_feature("payments", beta) is True


# ------------------------------------------------------------- no tenant --


def test_no_tenant_means_nothing_is_enabled():
    """The console host and management commands have no purchase to honour, and
    nothing tenant-scoped is reachable from there anyway."""
    assert enabled_features(None) == frozenset()


def test_the_context_supplies_the_tenant(tenant):
    with tenant_context(tenant):
        assert has_feature("payments") is True


# --------------------------------------------------------------- coverage --


def test_every_catalogue_key_resolves(tenant):
    resolved = enabled_features(tenant)
    assert resolved <= FEATURE_KEYS
    for key in FEATURE_KEYS:
        assert isinstance(has_feature(key, tenant), bool)


def test_a_plan_with_no_rows_still_returns_core():
    plan = Plan.objects.create(slug="empty", name="Empty")
    tenant = make_tenant("alpha", plan=plan)
    assert set(CORE_KEYS) <= enabled_features(tenant)
