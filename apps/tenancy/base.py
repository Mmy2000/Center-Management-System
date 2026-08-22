"""The base class that makes tenant filtering the default (docs/10 §N.4, TASK-093).

The whole point of this module is that **no existing query has to change**.
There are 143 ``objects.`` call sites across the domain apps; filtering them by
hand is how one gets missed. Instead the *default manager* filters, and the
handful of places that genuinely need to cross tenants must type
``all_tenants`` — which is greppable, reviewable and tested for.

Separate from ``tenancy.models`` so that ``core.models`` can import it without
an import cycle (core defines ``TimeStampedModel``, tenancy's models import
core). This module imports only ``core.base``, which imports nothing.
"""

from django.db import models

from apps.core.base import TimeStampedModel

from .context import current_tenant
from .exceptions import CrossTenantWrite, TenantContextRequired


class TenantQuerySet(models.QuerySet):
    """Adds the write-side guarantees ``save()`` cannot give us.

    ``bulk_create`` bypasses ``Model.save()`` entirely, so the tenant stamp that
    ``TenantOwnedModel.save()`` applies would never run — and the card importer
    and the lesson generator both go through it. Stamping here closes that hole
    at the only layer both paths share.
    """

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        if objs:
            tenant = current_tenant()
            for obj in objs:
                if obj.tenant_id is None:
                    if tenant is None:
                        raise TenantContextRequired(
                            f"bulk_create of {self.model.__name__} with no tenant in context"
                        )
                    obj.tenant = tenant
                elif tenant is not None and obj.tenant_id != tenant.pk:
                    raise CrossTenantWrite(
                        f"bulk_create of {self.model.__name__} carrying tenant "
                        f"{obj.tenant_id} while {tenant.pk} is in context"
                    )
        return super().bulk_create(objs, *args, **kwargs)


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """The default manager: every read is scoped to the tenant in context.

    A missing tenant raises instead of returning ``.none()``. An empty result is
    indistinguishable from a quiet day — a report showing zero students would
    look plausible and ship. A traceback does not.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        tenant = current_tenant()
        if tenant is None:
            raise TenantContextRequired(
                f"{self.model.__name__}.objects was used with no tenant in context. "
                f"Wrap the call in tenant_context(tenant), or use "
                f"{self.model.__name__}.all_tenants if crossing tenants is intended."
            )
        return queryset.filter(tenant_id=tenant.pk)


class AllTenantsManager(models.Manager.from_queryset(models.QuerySet)):
    """Unfiltered access. Only ``apps.tenancy``, ``apps.console`` and migrations
    may use it, and a test in the leak suite enforces that."""


class TenantOwnedModel(TimeStampedModel):
    """Every model that belongs to one center.

    ``objects`` is declared **first** so it becomes ``_default_manager`` — which
    is what the Django admin, ``ModelChoiceField``, ``get_object_or_404`` and
    every piece of generic code reaches for. Those are exactly the places that
    must not see another center's rows.

    ``Meta.base_manager_name`` is deliberately **not** set. Django's default
    ``_base_manager`` is a plain unfiltered ``Manager`` it creates on the fly,
    which is exactly what forward FK traversal (``lesson.group``) and
    ``refresh_from_db()`` need — and, unlike a named base manager, it cannot be
    silently lost when a subclass declares its own ``class Meta``. Naming one
    here would be redundant at best and a footgun across twenty models at worst.

    ``on_delete=PROTECT`` because deleting a center must be a deliberate console
    operation that exports first, never a cascade someone triggers from a shell.
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        related_name="+",
        db_index=True,
        editable=False,
        verbose_name="العميل",
    )

    objects = TenantManager()
    all_tenants = AllTenantsManager()

    class Meta:
        abstract = True

    def _stamp_tenant(self):
        """Fill in ``tenant`` from context, or refuse a mismatch."""
        tenant = current_tenant()
        if self.tenant_id is None:
            if tenant is None:
                raise TenantContextRequired(
                    f"Saving {type(self).__name__} with no tenant set and none in context"
                )
            self.tenant = tenant
        elif tenant is not None and self.tenant_id != tenant.pk:
            raise CrossTenantWrite(
                f"{type(self).__name__} carries tenant {self.tenant_id} "
                f"while tenant {tenant.pk} is in context"
            )

    def _check_foreign_keys(self):
        """Refuse a FK pointing at another center's row.

        The database cannot express this without composite foreign keys, so it
        is enforced here and re-checked by the leak suite. Only *loaded* related
        objects and cached ids are inspected — this must not fire a query per FK
        on the scan path.
        """
        for field in self._meta.concrete_fields:
            if not field.is_relation or field.name == "tenant":
                continue
            related_model = field.related_model
            if not issubclass(related_model, TenantOwnedModel):
                continue
            if getattr(self, field.attname) is None:
                continue
            related = self._get_loaded_related(field)
            if related is not None and related.tenant_id != self.tenant_id:
                raise CrossTenantWrite(
                    f"{type(self).__name__}.{field.name} points at "
                    f"{related_model.__name__} of tenant {related.tenant_id}, "
                    f"but this row belongs to tenant {self.tenant_id}"
                )

    def _get_loaded_related(self, field):
        """The related instance if it is already in memory, else ``None``."""
        return field.get_cached_value(self, default=None)

    def save(self, *args, **kwargs):
        self._stamp_tenant()
        self._check_foreign_keys()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self._state.adding:
            # Defensive: an insert with update_fields would drop the stamp.
            kwargs["update_fields"] = None
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.tenant_id is not None:
            self._check_foreign_keys()
