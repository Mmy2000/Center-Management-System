"""What did this center buy? (docs/10 §N.8, TASK-107)

    from apps.tenancy.resolver import has_feature

    has_feature("payments")            # -> bool, for the tenant in context

Resolution order, once per request and then cached:

    TenantFeature.state == ON    -> True     explicit per-client override
    TenantFeature.state == OFF   -> False
    no override / INHERIT        -> the key is in the tenant's plan
    the plan says nothing        -> FeatureSpec.default
    any depends_on is False      -> False    (applied transitively)
    FeatureSpec.is_core          -> always True

The whole answer is one ``frozenset`` cached per tenant, because the scan path
gets six queries (docs/README principle 6) and none of them may go on asking
whether a feature is on. Every write that could change the answer invalidates
the key, so the cache is never the reason a toggle takes five minutes to bite.

Redis matters here. With LocMemCache and four gunicorn workers, a feature you
just disabled stays on in three of them until the TTL expires — which is why
docs/10 §N.13 makes Redis mandatory above one worker.
"""

import logging

from django.core.cache import cache

from .context import current_tenant
from .features import CORE_KEYS, FEATURES, spec_for

logger = logging.getLogger("tenancy")

CACHE_PREFIX = "tenancy:features:"
CACHE_TTL = 300


def cache_key(tenant_id: int) -> str:
    return f"{CACHE_PREFIX}{tenant_id}"


def _plan_keys(tenant) -> frozenset[str]:
    plan_id = getattr(tenant, "plan_id", None)
    if plan_id is None:
        return frozenset()
    from .models import PlanFeature

    return frozenset(
        PlanFeature.objects.filter(plan_id=plan_id).values_list("feature_key", flat=True)
    )


def _overrides(tenant) -> dict[str, str]:
    from .constants import FeatureState
    from .models import TenantFeature

    return {
        key: state
        for key, state in TenantFeature.objects.filter(tenant_id=tenant.pk).values_list(
            "feature_key", "state"
        )
        if state != FeatureState.INHERIT
    }


def _resolve(tenant) -> frozenset[str]:
    """Compute the answer from the database. Callers should use the cache."""
    from .constants import FeatureState

    plan_keys = _plan_keys(tenant)
    overrides = _overrides(tenant)

    enabled = set()
    for spec in FEATURES:
        state = overrides.get(spec.key)
        if state == FeatureState.ON:
            on = True
        elif state == FeatureState.OFF:
            on = False
        elif plan_keys:
            on = spec.key in plan_keys
        else:
            # A tenant whose plan lists nothing gets the catalogue defaults.
            # That is the honest reading of "no plan data" and it is what the
            # installation migrated from single-tenant runs on.
            on = spec.default
        if on:
            enabled.add(spec.key)

    # Dependencies, transitively: switching `payments` off has to take the
    # refunds, the receipts and the financial reports with it. Iterate to a
    # fixed point rather than one pass, so a chain of any depth collapses.
    changed = True
    while changed:
        changed = False
        for key in list(enabled):
            if any(dependency not in enabled for dependency in spec_for(key).depends_on):
                enabled.discard(key)
                changed = True

    # Core last: nothing above may switch off what the product cannot run
    # without, and the console renders those toggles disabled rather than
    # hiding them so an operator can see the switch exists.
    enabled |= set(CORE_KEYS)
    return frozenset(enabled)


def enabled_features(tenant=None) -> frozenset[str]:
    """Every feature key that is on for ``tenant`` (default: the one in context)."""
    tenant = tenant or current_tenant()
    if tenant is None:
        # No tenant means no purchase to honour: the console host, a management
        # command, the login page of an unresolved host. Nothing is enabled, and
        # nothing tenant-scoped is reachable there anyway.
        return frozenset()

    key = cache_key(tenant.pk)
    cached = cache.get(key)
    if cached is not None:
        return cached

    resolved = _resolve(tenant)
    cache.set(key, resolved, CACHE_TTL)
    return resolved


def has_feature(feature_key: str, tenant=None) -> bool:
    """Whether ``feature_key`` is on. An unknown key raises, never returns False.

    A silent ``False`` would disable a feature nobody meant to disable, and the
    symptom (a missing menu entry) is a long way from the cause (a typo in a
    decorator).
    """
    spec_for(feature_key)  # typo protection
    return feature_key in enabled_features(tenant)


def invalidate(tenant_id: int) -> None:
    cache.delete(cache_key(tenant_id))


def invalidate_plan(plan_id: int) -> None:
    """Every tenant on ``plan_id`` has to re-resolve."""
    from .models import Tenant

    ids = Tenant.objects.filter(plan_id=plan_id).values_list("pk", flat=True)
    cache.delete_many([cache_key(tenant_id) for tenant_id in ids])
