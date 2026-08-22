"""Tenant-aware user lookup (docs/10 §N.6, TASK-094).

``User`` is the one model that is deliberately *not* ``TenantOwnedModel``: a
user belongs to one center, or to none at all when they are platform staff. So
it needs its own manager rather than the strict one, and the rule it follows is:

    a tenant in context  ->  that tenant's users
    no tenant in context ->  the platform's users (tenant IS NULL)

That second line is not a silent fallback — "no tenant" *is* the platform scope,
and the platform's users are exactly the rows with a null tenant. It is what
makes ``createsuperuser`` work from a bare shell, and what stops the console
from ever listing a center's staff by accident.

Reaching across every tenant at once stays explicit: ``User.all_tenants``.
"""

from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models

from apps.tenancy.context import current_tenant


class TenantUserQuerySet(models.QuerySet):
    def platform_staff(self):
        return self.filter(tenant__isnull=True, is_platform_staff=True)

    def for_tenant(self, tenant):
        return self.filter(tenant=tenant)


class TenantUserManager(DjangoUserManager.from_queryset(TenantUserQuerySet)):
    """The default manager: scoped to the tenant in context, or to the platform."""

    def get_queryset(self):
        queryset = super().get_queryset()
        tenant = current_tenant()
        if tenant is None:
            return queryset.filter(tenant__isnull=True)
        return queryset.filter(tenant_id=tenant.pk)

    def _create_user(self, username, email=None, password=None, **extra_fields):
        """Bind a new user to the center they are being created in.

        ``User`` is not a ``TenantOwnedModel``, so nothing stamps it on save.
        Doing it here means every existing ``create_user`` call site — services,
        the seed commands, the test factories — keeps working unchanged and
        lands in the right center.
        """
        extra_fields.setdefault("tenant", current_tenant())
        return super()._create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        """A superuser created outside any center is platform staff.

        That is what ``manage.py createsuperuser`` does on a fresh install, and
        it is the only way the first console account can exist.
        """
        if current_tenant() is None:
            extra_fields.setdefault("is_platform_staff", True)
        return super().create_superuser(username, email, password, **extra_fields)


class AllTenantsUserManager(DjangoUserManager.from_queryset(TenantUserQuerySet)):
    """Unfiltered. Console, provisioning and migrations only."""
