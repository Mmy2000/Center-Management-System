"""Cache invalidation for the control plane (TASK-092, TASK-107).

Three caches are read on the hot path and must never be the reason a console
change takes five minutes to bite: the host → tenant map, the per-tenant set of
enabled features, and the per-tenant traffic policy. All three are dropped on
any write that could change the answer, so the TTL is a backstop rather than
the mechanism.

The traffic policy matters most of the three: an operator blocking a client
that is currently flooding the server needs it to stop *now*, not within five
minutes, and an operator lifting a block needs the center working again before
they have finished telling them it is.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Domain, Plan, PlanFeature, Tenant, TenantFeature, TenantRatePolicy
from .resolution import invalidate_host, invalidate_tenant
from .resolver import invalidate as invalidate_features
from .resolver import invalidate_plan


@receiver(post_save, sender=Domain)
@receiver(post_delete, sender=Domain)
def _invalidate_domain(sender, instance, **kwargs):
    invalidate_host(instance.host)


@receiver(post_save, sender=Tenant)
def _invalidate_tenant(sender, instance, **kwargs):
    # Status, plan and language all ride on the cached tenant row; a change of
    # plan also changes which features resolve.
    invalidate_tenant(instance)
    invalidate_features(instance.pk)


@receiver(post_save, sender=TenantFeature)
@receiver(post_delete, sender=TenantFeature)
def _invalidate_override(sender, instance, **kwargs):
    """A toggle in the console must take effect on the client's next request."""
    invalidate_features(instance.tenant_id)


@receiver(post_save, sender=PlanFeature)
@receiver(post_delete, sender=PlanFeature)
def _invalidate_plan_feature(sender, instance, **kwargs):
    invalidate_plan(instance.plan_id)


@receiver(post_save, sender=Plan)
def _invalidate_plan(sender, instance, **kwargs):
    invalidate_plan(instance.pk)


@receiver(post_save, sender=TenantRatePolicy)
@receiver(post_delete, sender=TenantRatePolicy)
def _invalidate_rate_policy(sender, instance, **kwargs):
    """Blocking or unblocking a client must bite on their very next request.

    The policy is cached *inside* the tenant row (``resolve_host`` selects it
    alongside), so dropping the host keys is what drops the policy — one cache,
    one invalidation, and no way for the two to disagree.
    """
    invalidate_tenant_for(instance.tenant_id)


def invalidate_tenant_for(tenant_id: int) -> None:
    """``invalidate_tenant`` by id, for signals that only have the FK."""
    from .models import Tenant

    tenant = Tenant.objects.filter(pk=tenant_id).first()
    if tenant is not None:
        invalidate_tenant(tenant)
