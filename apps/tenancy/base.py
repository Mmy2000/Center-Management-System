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
    """Tenant filtering, plus the write-side guarantees ``save()`` cannot give.

    **Deferred resolution.** Normally the manager filters eagerly and this flag
    is never set. The exception is import time: a ``ModelForm`` builds each
    ``ModelChoiceField`` when the class is *defined*, calling
    ``Model._default_manager.using(...)`` before any request — and therefore
    before any tenant — exists. Raising there would make the app unimportable;
    filtering by "no tenant" would bake an empty choice list into the field
    forever. So a queryset built outside a tenant is marked deferred and
    resolves its tenant when it is finally used, which for a form field is
    inside a request. ``ModelChoiceField.__deepcopy__`` clones the queryset per
    form instance, so each request resolves its own tenant.

    Every path that reaches the database is covered: iteration, ``count()``,
    ``exists()``, ``aggregate()``, ``update()``, ``delete()``, ``iterator()``
    — and ``resolve_expression()``, which is how a queryset used as a subquery
    (``filter(x__in=qs)``) would otherwise slip past unfiltered.

    ``bulk_create`` bypasses ``Model.save()`` entirely, so the tenant stamp that
    ``TenantOwnedModel.save()`` applies would never run — and the card importer
    and the lesson generator both go through it. Stamping here closes that hole
    at the only layer both paths share.
    """

    #: Set by TenantManager when it built this queryset with no tenant around.
    _tenant_deferred = False

    def _clone(self):
        clone = super()._clone()
        clone._tenant_deferred = self._tenant_deferred
        return clone

    def _resolve_tenant(self):
        """Apply the deferred tenant filter, or refuse to run."""
        if not self._tenant_deferred:
            return
        tenant = current_tenant()
        if tenant is None:
            raise TenantContextRequired(
                f"{self.model.__name__}.objects was used with no tenant in context. "
                f"Wrap the call in tenant_context(tenant), or use "
                f"{self.model.__name__}.all_tenants if crossing tenants is intended."
            )
        # In place: this queryset is being consumed right now, and mutating the
        # clone Django already made is exactly what .filter() would have done.
        self._tenant_deferred = False
        self.query.add_q(models.Q(tenant_id=tenant.pk))

    # -- every route to the database ---------------------------------------
    def _fetch_all(self):
        self._resolve_tenant()
        return super()._fetch_all()

    def count(self):
        self._resolve_tenant()
        return super().count()

    def exists(self):
        self._resolve_tenant()
        return super().exists()

    def aggregate(self, *args, **kwargs):
        self._resolve_tenant()
        return super().aggregate(*args, **kwargs)

    def update(self, **kwargs):
        self._resolve_tenant()
        return super().update(**kwargs)

    def delete(self):
        self._resolve_tenant()
        return super().delete()

    def iterator(self, *args, **kwargs):
        self._resolve_tenant()
        return super().iterator(*args, **kwargs)

    def in_bulk(self, *args, **kwargs):
        self._resolve_tenant()
        return super().in_bulk(*args, **kwargs)

    def contains(self, obj):
        self._resolve_tenant()
        return super().contains(obj)

    def explain(self, *args, **kwargs):
        self._resolve_tenant()
        return super().explain(*args, **kwargs)

    def resolve_expression(self, *args, **kwargs):
        """Used as a subquery — resolve before the SQL is built, not after."""
        self._resolve_tenant()
        return super().resolve_expression(*args, **kwargs)

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

    Inside a tenant the filter is applied **eagerly**, which is what makes
    subqueries, prefetches and every other queryset composition correct without
    thinking about it. Outside one the queryset is marked deferred instead of
    raising here, because Django builds ``ModelForm`` fields at import time —
    see :class:`TenantQuerySet`. Either way, a query that reaches the database
    without a tenant raises; it never quietly returns nothing, because an empty
    result is indistinguishable from a quiet Saturday and would ship.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        tenant = current_tenant()
        if tenant is None:
            queryset._tenant_deferred = True
            return queryset
        return queryset.filter(tenant_id=tenant.pk)


class AllTenantsManager(models.Manager.from_queryset(models.QuerySet)):
    """Unfiltered access. Only ``apps.tenancy``, ``apps.console`` and migrations
    may use it, and a test in the leak suite enforces that."""


def _has_tenant_field(model) -> bool:
    """Whether ``model`` carries a ``tenant`` column at all."""
    try:
        model._meta.get_field("tenant")
    except Exception:
        return False
    return True


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
        objects are inspected — this must not fire a query per FK on the scan
        path, whose whole budget is six.

        Any related model carrying a ``tenant`` field counts, not only
        ``TenantOwnedModel`` subclasses: ``accounts.User`` has a nullable one and
        is the FK behind ``created_by`` on half the domain. A *null* tenant on
        the far side is allowed — that is platform staff acting inside a center,
        which impersonation makes legitimate (TASK-115).
        """
        for field in self._meta.concrete_fields:
            if not field.is_relation or field.name == "tenant":
                continue
            related_model = field.related_model
            if related_model is None or not _has_tenant_field(related_model):
                continue
            if getattr(self, field.attname) is None:
                continue
            related = self._get_loaded_related(field)
            if related is None:
                continue
            related_tenant_id = getattr(related, "tenant_id", None)
            if related_tenant_id is not None and related_tenant_id != self.tenant_id:
                raise CrossTenantWrite(
                    f"{type(self).__name__}.{field.name} points at "
                    f"{related_model.__name__} of tenant {related_tenant_id}, "
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
        # Stamp early so the per-tenant unique constraints below have a tenant
        # to check against. Tolerant of a missing context — save() is where an
        # absent tenant becomes an error; a form must not blow up in clean().
        if self.tenant_id is None and current_tenant() is not None:
            self.tenant = current_tenant()
        if self.tenant_id is not None:
            self._check_foreign_keys()

    # -- keeping the per-tenant unique constraints enforceable in forms -----
    #
    # `tenant` is editable=False, so it never appears in a ModelForm, so the
    # form adds it to the validation exclusions — and Django skips any
    # constraint that mentions an excluded field. That would silently disable
    # every UNIQUE(tenant, …) constraint at the form layer and turn a duplicate
    # student code into a 500 at INSERT instead of a field error.
    #
    # Un-excluding it is safe precisely because it is not user input: the value
    # comes from the request's tenant and is already stamped by clean() above.

    def _unexclude_tenant(self, exclude):
        if exclude and "tenant" in exclude and self.tenant_id is not None:
            return {name for name in exclude if name != "tenant"}
        return exclude

    def validate_unique(self, exclude=None):
        return super().validate_unique(exclude=self._unexclude_tenant(exclude))

    def validate_constraints(self, exclude=None):
        """Report a ``UNIQUE(tenant, x)`` violation as an error on ``x``.

        Django keys a multi-field constraint violation under ``__all__``, which
        is right when a user chose both fields. Here they chose one: ``tenant``
        is invisible to them and came from the hostname. So a duplicate student
        code should highlight the student-code input and read "a student with
        this code already exists" — exactly what ``unique=True`` produced before
        tenancy — rather than a form-wide "student with this Client and this
        Code already exists".

        Constraints of other shapes (partial, three-field, non-unique) are left
        entirely to Django.
        """
        from django.core.exceptions import ValidationError

        exclude = set(self._unexclude_tenant(exclude) or ())
        errors: dict[str, list] = {}
        handled: set[str] = set()

        if self.tenant_id is not None:
            for constraint in self._meta.constraints:
                fields = tuple(getattr(constraint, "fields", None) or ())
                if len(fields) != 2 or fields[0] != "tenant":
                    continue
                if getattr(constraint, "condition", None) is not None:
                    continue
                target = fields[1]
                if target in exclude:
                    continue
                handled.add(target)
                try:
                    constraint.validate(type(self), self, exclude=exclude or None)
                except ValidationError:
                    # Re-message as a single-field violation, so the wording
                    # names the field the user actually typed into.
                    errors.setdefault(target, []).append(
                        self.unique_error_message(type(self), (target,))
                    )

        try:
            super().validate_constraints(exclude=(exclude | handled) or None)
        except ValidationError as exc:
            errors = exc.update_error_dict(errors)

        if errors:
            raise ValidationError(errors)
