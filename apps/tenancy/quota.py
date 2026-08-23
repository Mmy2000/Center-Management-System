"""Plan limits (docs/10 §N.9, TASK-109).

    quota.check("students")          # raises when the plan is full

Enforced in the **creation services only**, never in a signal. A signal fires
during the Phase 14 backfill and during a restore, where the limit is not just
irrelevant but actively wrong — refusing to restore a client's own data because
they have since been moved to a smaller plan would be absurd.

``None`` on the plan means unlimited.

**A limit decision always counts for real.** ``TenantUsage`` is a cache for the
console list, and a cache is exactly wrong here: create a student, and the
cached number is one behind for the rest of its freshness window — long enough
to walk straight past a limit of one. A ``COUNT(*)`` on an indexed ``tenant_id``
is cheap, and creating a student is not the hot path; the scan path is, and it
never asks about quotas.
"""

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from apps.core.http import DomainError

from .context import current_tenant

#: resource -> (app_label, model, TenantUsage field). The plan's column is
#: always ``max_<resource>``.
RESOURCES = {
    "students": ("students", "Student", "students"),
    "users": ("accounts", "User", "users"),
    "groups": ("academics", "Group", "groups"),
    "cards": ("cards", "StudentCard", "cards"),
}

#: How long a TenantUsage row may be trusted for a *limit* decision. Short, on
#: purpose: the console can afford a minute-old number, a quota cannot.
FRESH_SECONDS = 60


def _live_count(tenant, resource: str) -> int:
    from django.apps import apps as django_apps

    app_label, model_name, _field = RESOURCES[resource]
    model = django_apps.get_model(app_label, model_name)
    manager = getattr(model, "all_tenants", model._base_manager)
    return manager.filter(tenant=tenant).count()


def _refresh(tenant, resource: str, count: int) -> None:
    """Keep the console's cached number in step, since we just counted anyway."""
    from .models import TenantUsage

    row, _created = TenantUsage.objects.get_or_create(tenant=tenant)
    setattr(row, RESOURCES[resource][2], count)
    row.computed_at = timezone.now()
    row.save()


def usage(tenant, resource: str, *, live: bool = False) -> int:
    """How many of ``resource`` this center already has.

    ``live=True`` for anything that gates a write; the cached value is for
    display only. See the module docstring for why the distinction matters.
    """
    from .models import TenantUsage

    if not live:
        row = TenantUsage.objects.filter(tenant=tenant).first()
        if row is not None and not row.is_stale(FRESH_SECONDS):
            return getattr(row, RESOURCES[resource][2])

    count = _live_count(tenant, resource)
    _refresh(tenant, resource, count)
    return count


def limit(tenant, resource: str) -> int | None:
    plan = getattr(tenant, "plan", None)
    if plan is None:
        return None
    return getattr(plan, f"max_{resource}", None)


def remaining(tenant, resource: str) -> int | None:
    """Headroom, or ``None`` for unlimited."""
    cap = limit(tenant, resource)
    if cap is None:
        return None
    return max(0, cap - usage(tenant, resource))


#: Lazy, not eager: this dict is built at import time, and `gettext` there
#: would freeze whatever language the worker started in.
LABELS = {
    "students": gettext_lazy("طالب"),
    "users": gettext_lazy("مستخدم"),
    "groups": gettext_lazy("مجموعة"),
    "cards": gettext_lazy("بطاقة"),
}


def check(resource: str, count: int = 1, *, tenant=None) -> None:
    """Refuse when ``count`` more would exceed the plan.

    The message names the limit and the plan, and tells the center admin to
    contact the operator — it does not invite them to "upgrade", because there
    is no self-serve upgrade to click: you sell and invoice offline
    (docs/10 §N.14).
    """
    tenant = tenant or current_tenant()
    if tenant is None:
        return  # no tenant, no plan, no limit — migrations and the console

    cap = limit(tenant, resource)
    if cap is None:
        return

    current = usage(tenant, resource, live=True)
    if current + count <= cap:
        return

    raise DomainError(
        "ERR_QUOTA_EXCEEDED",
        _(
            "تم بلوغ الحد الأقصى (%(cap)s %(label)s) في باقة %(plan)s. برجاء التواصل مع مشغّل النظام."
        )
        % {"cap": cap, "label": LABELS.get(resource, resource), "plan": tenant.plan.name},
        status=409,
        data={
            "resource": resource,
            "limit": cap,
            "current": current,
            "requested": count,
            "available": max(0, cap - current),
        },
    )


def invalidate(tenant) -> None:
    """Mark the console's cached numbers stale.

    Limit *decisions* never needed this — they always count live — but the
    console's list should not show yesterday's totals after a bulk change.
    """
    from .models import TenantUsage

    TenantUsage.objects.filter(tenant=tenant).update(computed_at=None)
