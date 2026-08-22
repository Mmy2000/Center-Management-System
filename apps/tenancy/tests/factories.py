"""Fixtures shared by every tenancy test (and, from TASK-119, by the leak suite).

The leak suite's whole method is *two centers holding identical data* — same
student codes, same card numbers, same usernames — so that any row crossing the
boundary is unambiguous rather than merely suspicious. These helpers exist to
make that pairing one line.
"""

from apps.tenancy.constants import TenantStatus
from apps.tenancy.features import FEATURES
from apps.tenancy.models import Domain, Plan, PlanFeature, Tenant


def make_plan(slug="full", *, features=None, **kwargs) -> Plan:
    """A plan carrying every feature unless told otherwise."""
    defaults = {
        "name": slug.title(),
        "max_students": None,
        "max_users": None,
        "max_groups": None,
        "max_cards": None,
    }
    defaults.update(kwargs)
    plan, _created = Plan.objects.get_or_create(slug=slug, defaults=defaults)
    keys = [spec.key for spec in FEATURES] if features is None else list(features)
    existing = set(plan.features.values_list("feature_key", flat=True))
    PlanFeature.objects.bulk_create(
        [PlanFeature(plan=plan, feature_key=key) for key in keys if key not in existing]
    )
    return plan


def make_tenant(
    slug="alpha",
    *,
    name=None,
    status=TenantStatus.ACTIVE,
    plan=None,
    host=None,
    **kwargs,
) -> Tenant:
    """A tenant with a primary domain, ready to serve requests."""
    tenant = Tenant.objects.create(
        slug=slug,
        name=name or f"سنتر {slug}",
        status=status,
        plan=plan or make_plan(),
        **kwargs,
    )
    Domain.objects.create(
        tenant=tenant,
        host=host or f"{slug}.testserver",
        is_primary=True,
    )
    return tenant


def make_two_tenants():
    """The pair every isolation test is built on."""
    plan = make_plan()
    return (
        make_tenant("alpha", plan=plan, host="alpha.testserver"),
        make_tenant("beta", plan=plan, host="beta.testserver"),
    )
