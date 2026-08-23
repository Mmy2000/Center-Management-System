"""JSON endpoints for the console (TASK-112/114/117).

The console's screens fetch through the same envelope and the same
``static/js/http.js`` wrapper the tenant UI uses (docs/05 §G.2), so the loading
bar, the toasts and the error handling are the ones already built — nothing here
reimplements them.

``console_ajax`` is ``core.http.ajax`` plus the console's own two-wall guard:
the request must have arrived on the console host, and the user must be platform
staff. Both, always, even though the URLconf already makes the first structurally
true — a wall you only built once is a wall you are trusting too much.
"""

from functools import wraps

from django.db.models import Count, Q, Sum
from django.http import Http404
from django.utils import timezone

from apps.core.http import ajax, fail
from apps.tenancy import quota
from apps.tenancy.constants import OPERATIONAL_STATUSES, FeatureState, TenantStatus
from apps.tenancy.models import Plan, PlatformAuditLog, Tenant
from apps.tenancy.resolver import enabled_features

from . import services


def console_ajax(methods=("GET",)):
    def decorator(view):
        @ajax(methods=methods)
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not getattr(request, "is_console", False):
                raise Http404("not the console host")
            if not request.user.is_platform_staff:
                return fail("ERR_FORBIDDEN", "غير مسموح", status=404)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator


def quota_pressure(tenant) -> int:
    """Highest percentage of any plan limit this client is using.

    Read from the cached ``TenantUsage`` row, never live: this runs once per
    client on a list page, and a hundred live counts would make the console the
    slowest page in the product.
    """
    usage = getattr(tenant, "usage", None)
    if usage is None:
        return 0
    worst = 0
    for resource in quota.RESOURCES:
        cap = getattr(tenant.plan, f"max_{resource}", None)
        if not cap:
            continue
        used = getattr(usage, quota.RESOURCES[resource][2], 0)
        worst = max(worst, int(used * 100 / cap))
    return worst


def tenant_json(tenant) -> dict:
    usage = getattr(tenant, "usage", None)
    days = tenant.days_until_expiry()
    return {
        "id": tenant.pk,
        "slug": tenant.slug,
        "name": tenant.name,
        "host": tenant.primary_host,
        "status": tenant.status,
        "status_label": tenant.get_status_display(),
        "operational": tenant.is_operational,
        "plan": tenant.plan.name,
        "plan_slug": tenant.plan.slug,
        "students": getattr(usage, "students", 0),
        "users": getattr(usage, "users", 0),
        "groups": getattr(usage, "groups", 0),
        "cards": getattr(usage, "cards", 0),
        "pressure": quota_pressure(tenant),
        "expires_at": tenant.expires_at.date().isoformat() if tenant.expires_at else None,
        "trial_ends_at": (
            tenant.trial_ends_at.date().isoformat() if tenant.trial_ends_at else None
        ),
        "days_left": days,
        "created_at": tenant.created_at.date().isoformat(),
    }


@console_ajax(methods=["GET"])
def tenants(request):
    """The client list, filtered. One query plus the prefetch, at any scale."""
    queryset = Tenant.objects.select_related("plan", "usage").prefetch_related("domains")

    status = request.GET.get("status")
    if status:
        queryset = queryset.filter(status=status)
    plan = request.GET.get("plan")
    if plan:
        queryset = queryset.filter(plan__slug=plan)
    if request.GET.get("expiring") == "1":
        soon = timezone.now() + timezone.timedelta(days=30)
        queryset = queryset.filter(
            Q(expires_at__isnull=False, expires_at__lte=soon)
            | Q(trial_ends_at__isnull=False, trial_ends_at__lte=soon, status=TenantStatus.TRIAL)
        )

    term = (request.GET.get("q") or "").strip()
    if term:
        queryset = queryset.filter(
            Q(name__icontains=term)
            | Q(slug__icontains=term)
            | Q(owner_email__icontains=term)
            | Q(owner_name__icontains=term)
            | Q(domains__host__icontains=term)
        ).distinct()

    rows = [tenant_json(t) for t in queryset.order_by("name")]
    if request.GET.get("over_quota") == "1":
        rows = [row for row in rows if row["pressure"] >= 90]

    return {"results": rows, "count": len(rows)}


@console_ajax(methods=["GET"])
def overview(request):
    """Dashboard figures, all from cached usage rows."""
    now = timezone.now()
    soon = now + timezone.timedelta(days=30)

    by_status = dict(
        Tenant.objects.values_list("status").annotate(n=Count("pk")).values_list("status", "n")
    )
    totals = Tenant.objects.aggregate(
        students=Sum("usage__students"),
        users=Sum("usage__users"),
        scans=Sum("usage__scans_30d"),
        collected=Sum("usage__payments_30d_total"),
    )

    expiring = (
        Tenant.objects.select_related("plan", "usage")
        .filter(status__in=OPERATIONAL_STATUSES)
        .filter(
            Q(expires_at__isnull=False, expires_at__lte=soon)
            | Q(trial_ends_at__isnull=False, trial_ends_at__lte=soon, status=TenantStatus.TRIAL)
        )
        .order_by("expires_at", "trial_ends_at")[:8]
    )

    pressured = [
        tenant_json(t)
        for t in Tenant.objects.select_related("plan", "usage")
        if quota_pressure(t) >= 80
    ]

    return {
        "statuses": [
            {"value": value, "label": str(label), "count": by_status.get(value, 0)}
            for value, label in TenantStatus.choices
        ],
        "total": sum(by_status.values()),
        "students": totals["students"] or 0,
        "users": totals["users"] or 0,
        "scans": totals["scans"] or 0,
        "collected": str(totals["collected"] or 0),
        "new_this_month": Tenant.objects.filter(created_at__gte=now.replace(day=1)).count(),
        "expiring": [tenant_json(t) for t in expiring],
        "pressured": sorted(pressured, key=lambda r: -r["pressure"])[:8],
        "audit": [
            {
                "when": entry.created_at.strftime("%Y-%m-%d %H:%M"),
                "action": entry.get_action_display(),
                "tenant": entry.tenant.name if entry.tenant else None,
                "tenant_id": entry.tenant_id,
                "actor": entry.actor.display_name if entry.actor else "system",
                "reason": entry.reason,
            }
            for entry in PlatformAuditLog.objects.select_related("actor", "tenant")[:12]
        ],
    }


@console_ajax(methods=["POST"])
def tenant_status(request, pk):
    """Suspend, resume or archive — the reason is required and audited."""
    tenant = _get_tenant(pk)
    action = request.json.get("action")
    reason = (request.json.get("reason") or "").strip()

    if action != "resume" and len(reason) < 4:
        return fail(
            "ERR_VALIDATION",
            "اكتب سببًا واضحًا.",
            status=400,
            field_errors={"reason": ["السبب مطلوب"]},
        )

    if action == "suspend":
        services.suspend(tenant, reason=reason, actor=request.user)
        message = "تم إيقاف العميل. البيانات كما هي."
    elif action == "resume":
        services.resume(tenant, reason=reason, actor=request.user)
        message = "تم إعادة تفعيل العميل."
    elif action == "archive":
        services.archive(tenant, reason=reason, actor=request.user)
        message = "تمت أرشفة العميل."
    else:
        return fail("ERR_VALIDATION", "إجراء غير معروف", status=400)

    tenant.refresh_from_db()
    return {"tenant": tenant_json(tenant), "message": message}


@console_ajax(methods=["POST"])
def tenant_usage(request, pk):
    """Recount everything for one client, for real."""
    tenant = _get_tenant(pk)
    services.refresh_usage(tenant)
    tenant.refresh_from_db()
    return {"tenant": tenant_json(tenant), "usage": _usage_json(tenant)}


@console_ajax(methods=["POST"])
def tenant_plan(request, pk):
    """Move a client to another plan, warning first about what it costs them."""
    tenant = _get_tenant(pk)
    plan = Plan.objects.filter(pk=request.json.get("plan")).first()
    if plan is None:
        return fail("ERR_VALIDATION", "باقة غير معروفة", status=400)

    warnings = services.plan_change_warnings(tenant, plan)
    if request.json.get("preview"):
        return {"warnings": warnings, "plan": plan.name}

    services.change_plan(
        tenant, plan, actor=request.user, reason=(request.json.get("reason") or "")
    )
    quota.invalidate(tenant)
    tenant.refresh_from_db()
    return {"tenant": tenant_json(tenant), "warnings": warnings, "message": "تم تغيير الباقة."}


@console_ajax(methods=["POST"])
def tenant_feature(request, pk):
    """Move one feature toggle; answer with the whole recomputed set.

    The whole set, not just this key: dependencies mean one switch can flip
    several rows, and telling the screen about only the clicked one would leave
    it showing something untrue.
    """
    from apps.core.http import DomainError

    tenant = _get_tenant(pk)
    key = request.json.get("feature_key") or ""
    state = request.json.get("state")
    if state not in FeatureState.values:
        return fail("ERR_VALIDATION", "حالة غير معروفة", status=400)

    try:
        services.set_feature(tenant, key, state, actor=request.user, note="")
    except KeyError:
        return fail("ERR_NOT_FOUND", "خاصية غير معروفة", status=404)
    except DomainError as exc:
        return fail(exc.code, exc.message, status=exc.status, data=exc.data)

    return {"effective": sorted(enabled_features(tenant)), "feature_key": key, "state": state}


@console_ajax(methods=["GET"])
def slug_check(request):
    """Live availability while the operator types, so the wizard never fails on
    something that could have been said a second earlier."""
    from django.conf import settings

    from apps.tenancy.models import Domain, validate_slug

    slug = (request.GET.get("slug") or "").strip().lower()
    if not slug:
        return {"ok": False, "reason": ""}

    try:
        validate_slug(slug)
    except Exception as exc:  # ValidationError
        return {"available": False, "reason": getattr(exc, "messages", [str(exc)])[0]}

    if Tenant.objects.filter(slug=slug).exists():
        return {"available": False, "reason": "هذا المعرّف مستخدم بالفعل."}

    host = services.host_for(slug, settings.TENANT_BASE_DOMAIN)
    if Domain.objects.filter(host=host).exists():
        return {"available": False, "reason": f"النطاق {host} مستخدم بالفعل."}

    return {"available": True, "host": host}


# --------------------------------------------------------------------------- #


def _get_tenant(pk) -> Tenant:
    tenant = Tenant.objects.select_related("plan", "usage").filter(pk=pk).first()
    if tenant is None:
        raise Http404("no such client")
    return tenant


def _usage_json(tenant) -> dict:
    usage = getattr(tenant, "usage", None)
    return {
        "rows": [
            {
                "resource": resource,
                "label": str(quota.LABELS[resource]),
                "used": getattr(usage, quota.RESOURCES[resource][2], 0),
                "limit": getattr(tenant.plan, f"max_{resource}", None),
            }
            for resource in quota.RESOURCES
        ],
        # Both halves can be missing: no usage row at all, or a row that has
        # been marked stale by `quota.invalidate`.
        "computed_at": (
            usage.computed_at.strftime("%Y-%m-%d %H:%M") if usage and usage.computed_at else None
        ),
    }
