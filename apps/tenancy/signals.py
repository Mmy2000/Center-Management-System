"""Cache invalidation for the control plane (TASK-092).

The host → tenant map is read on every request and cached for five minutes. A
domain added in the console must work now, and a tenant suspended in the console
must be locked out now — not in five minutes. Both are one `cache.delete`.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Domain, Tenant
from .resolution import invalidate_host, invalidate_tenant


@receiver(post_save, sender=Domain)
@receiver(post_delete, sender=Domain)
def _invalidate_domain(sender, instance, **kwargs):
    invalidate_host(instance.host)


@receiver(post_save, sender=Tenant)
def _invalidate_tenant(sender, instance, **kwargs):
    # Status, plan and language all ride on the cached tenant row.
    invalidate_tenant(instance)
