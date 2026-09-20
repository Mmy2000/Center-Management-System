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

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.http import ajax, fail
from apps.tenancy import quota, traffic
from apps.tenancy.constants import (
    OPERATIONAL_STATUSES,
    FeatureState,
    TenantStatus,
    TrafficMode,
)
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
                return fail("ERR_FORBIDDEN", _("غير مسموح"), status=404)
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
            _("اكتب سببًا واضحًا."),
            status=400,
            field_errors={"reason": [_("السبب مطلوب")]},
        )

    if action == "suspend":
        services.suspend(tenant, reason=reason, actor=request.user)
        message = _("تم إيقاف العميل. البيانات كما هي.")
    elif action == "resume":
        services.resume(tenant, reason=reason, actor=request.user)
        message = _("تم إعادة تفعيل العميل.")
    elif action == "archive":
        services.archive(tenant, reason=reason, actor=request.user)
        message = _("تمت أرشفة العميل.")
    else:
        return fail("ERR_VALIDATION", _("إجراء غير معروف"), status=400)

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
        return fail("ERR_VALIDATION", _("باقة غير معروفة"), status=400)

    warnings = services.plan_change_warnings(tenant, plan)
    if request.json.get("preview"):
        return {"warnings": warnings, "plan": plan.name}

    services.change_plan(
        tenant, plan, actor=request.user, reason=(request.json.get("reason") or "")
    )
    quota.invalidate(tenant)
    tenant.refresh_from_db()
    return {"tenant": tenant_json(tenant), "warnings": warnings, "message": _("تم تغيير الباقة.")}


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
        return fail("ERR_VALIDATION", _("حالة غير معروفة"), status=400)

    try:
        services.set_feature(tenant, key, state, actor=request.user, note="")
    except KeyError:
        return fail("ERR_NOT_FOUND", _("خاصية غير معروفة"), status=404)
    except DomainError as exc:
        return fail(exc.code, exc.message, status=exc.status, data=exc.data)

    return {"effective": sorted(enabled_features(tenant)), "feature_key": key, "state": state}


# --------------------------------------------------------------------------- #
# Traffic monitoring and control (TASK-122)
# --------------------------------------------------------------------------- #

#: Periods the screen offers, in minutes. Capped at an hour because the minute
#: slots only reach that far; the 24-hour figure travels beside every row
#: regardless, read from the much cheaper hour slots.
PERIODS = (1, 5, 15, 60)

#: An upper bound on what may be typed into a limit box. Not a technical
#: ceiling — a typo guard. Six zeros in the wrong place is an operator who
#: believes they set a limit and did not.
MAX_LIMIT = 10_000_000


def _period(request) -> int:
    try:
        requested = int(request.GET.get("minutes") or 5)
    except (TypeError, ValueError):
        return 5
    return requested if requested in PERIODS else 5


def _tone(snapshot, policy) -> str:
    """How loudly this row should shout, in one word the template can style.

    Four levels rather than a number, because an operator scanning forty rows
    is looking for the one that is wrong, not reading each one's arithmetic.
    """
    if policy.blocked:
        return "blocked"
    if snapshot.throttled:
        return "critical"
    ceiling = policy.limit_for(1)
    if ceiling and snapshot.rps >= ceiling * 0.9:
        return "critical"
    if snapshot.errors_5xx and snapshot.error_rate >= 5:
        return "warning"
    if ceiling and snapshot.rps >= ceiling * 0.7:
        return "warning"
    if snapshot.rps >= 20:
        return "busy"
    return "normal"


def _usage_versus_limit(snapshot, policy) -> dict | None:
    """Where this client sits against the tightest limit that binds them.

    One figure, not three: showing rps/rpm/rph side by side makes the operator
    work out which of them is about to bite. The answer is whichever is closest.
    """
    measured = {"rps": snapshot.rps, "rpm": snapshot.rpm, "rph": snapshot.day_total}
    worst = None
    for name, seconds, _field in traffic.WINDOWS:
        ceiling = policy.limit_for(seconds)
        if not ceiling:
            continue
        percent = int(min(999, measured[name] * 100 / ceiling))
        if worst is None or percent > worst["percent"]:
            worst = {
                "window": name,
                "used": measured[name],
                "limit": ceiling,
                "percent": percent,
            }
    return worst


def traffic_json(tenant, snapshot, policy) -> dict:
    """One row of the monitoring table.

    Everything here comes from the cache snapshot and the already-loaded tenant
    row — no per-row query and no per-row cache round-trip. That is the whole
    reason ``traffic.snapshot`` takes a list of ids rather than one.
    """
    return {
        "id": tenant.pk,
        "slug": tenant.slug,
        "name": tenant.name,
        "host": tenant.primary_host,
        "lifecycle": tenant.status,
        "lifecycle_label": tenant.get_status_display(),
        "operational": tenant.is_operational,
        "mode": policy.mode,
        "mode_label": str(TrafficMode(policy.mode).label),
        "tone": _tone(snapshot, policy),
        "rps": snapshot.rps,
        "rpm": snapshot.rpm,
        "period_total": snapshot.period_total,
        "period_minutes": snapshot.period_minutes,
        "day_total": snapshot.day_total,
        "ok": snapshot.ok_responses,
        "c4": snapshot.errors_4xx,
        "c5": snapshot.errors_5xx,
        "throttled": snapshot.throttled,
        "error_rate": snapshot.error_rate,
        "avg_ms": snapshot.avg_ms,
        "concurrency": snapshot.concurrency,
        "service_seconds": snapshot.service_seconds,
        "last_seen": snapshot.last_seen,
        # Just the totals: a sparkline is a shape, and splitting it by status
        # at 3px tall would be three shapes nobody can read. The status split
        # lives in the hero chart, where it has the height to mean something.
        "spark": [point["total"] for point in snapshot.series],
        "limits": {
            "max_rps": policy.max_rps,
            "max_rpm": policy.max_rpm,
            "max_rph": policy.max_rph,
            "burst": policy.burst,
            "blocked": policy.blocked,
            "blocked_reason": policy.blocked_reason,
        },
        "pressure": _usage_versus_limit(snapshot, policy),
    }


def _policies_for(tenants) -> dict:
    """Policy rows for a whole page of clients, in one query.

    ``traffic.policy_for`` is the hot-path reader and answers for one tenant
    out of cache; here we have a list, and a hundred cache reads to render one
    table is exactly the shape this screen must not have.
    """
    from apps.tenancy.models import TenantRatePolicy

    rows = {
        row.tenant_id: row.as_policy()
        for row in TenantRatePolicy.objects.filter(tenant__in=tenants)
    }
    return {tenant.pk: rows.get(tenant.pk, traffic.UNLIMITED) for tenant in tenants}


@console_ajax(methods=["GET"])
def traffic_overview(request):
    """Every client's current traffic, in two queries and one cache read.

    The two queries are the tenant list and its policy rows; the cache read is
    a single ``get_many`` covering every counter for every tenant on screen.
    Deliberately flat like that — a monitoring page that costs a round-trip per
    row becomes, at exactly the wrong moment, part of the load it exists to
    show you.
    """
    minutes = _period(request)

    # The buffered second is still in this worker's own memory. Without this
    # the operator's refresh would show figures a second stale and, worse, a
    # tenant that has just gone quiet as one that never started.
    traffic.flush()

    queryset = Tenant.objects.select_related("plan").prefetch_related("domains")
    lifecycle = request.GET.get("lifecycle")
    if lifecycle:
        queryset = queryset.filter(status=lifecycle)

    term = (request.GET.get("q") or "").strip()
    if term:
        queryset = queryset.filter(Q(name__icontains=term) | Q(slug__icontains=term))

    tenants = list(queryset.order_by("name"))
    policies = _policies_for(tenants)
    snapshots = traffic.snapshot([t.pk for t in tenants], minutes=minutes)

    rows = [
        traffic_json(
            tenant,
            snapshots.get(tenant.pk) or traffic.Snapshot(tenant_id=tenant.pk),
            policies[tenant.pk],
        )
        for tenant in tenants
    ]

    mode = request.GET.get("mode")
    if mode in TrafficMode.values:
        rows = [row for row in rows if row["mode"] == mode]
    if request.GET.get("active_only") == "1":
        rows = [row for row in rows if row["period_total"]]

    # Busiest first: the reason anyone opens this page is to find out who is
    # making the noise, and alphabetical order buries them.
    rows.sort(key=lambda row: (-row["rps"], -row["period_total"], row["name"]))

    # The hero chart sums whatever survived the filters, so it always describes
    # the same slice as the table under it. Summing a filtered view is the
    # point: "show me only the limited clients" should redraw the chart too.
    visible = {row["id"] for row in rows}
    timeline = _timeline(snapshots, visible)

    return {
        "results": rows,
        "count": len(rows),
        "minutes": minutes,
        "generated_at": timezone.now().strftime("%H:%M:%S"),
        "totals": {
            "rps": round(sum(row["rps"] for row in rows), 2),
            "requests": sum(row["period_total"] for row in rows),
            "errors": sum(row["c4"] + row["c5"] for row in rows),
            "throttled": sum(row["throttled"] for row in rows),
            "blocked": sum(1 for row in rows if row["mode"] == TrafficMode.BLOCKED),
            "limited": sum(1 for row in rows if row["mode"] == TrafficMode.LIMITED),
        },
        "timeline": timeline,
        "server": traffic.server_metrics(),
        "cache_shared": traffic.cache_is_shared(),
    }


def _timeline(snapshots, visible) -> list[dict]:
    """Platform-wide requests per minute, split 2xx / 4xx / 5xx.

    Summed here rather than read separately: every tenant's per-minute shape
    already came back in the one ``get_many`` the list needed, so the headline
    chart costs arithmetic and not a single extra key.
    """
    buckets: dict[int, dict] = {}
    for tenant_id, snap in snapshots.items():
        if tenant_id not in visible:
            continue
        for point in snap.series:
            slot = buckets.setdefault(
                point["minute"], {"minute": point["minute"], "ok": 0, "c4": 0, "c5": 0}
            )
            slot["c4"] += point["c4"]
            slot["c5"] += point["c5"]
            slot["ok"] += max(0, point["total"] - point["c4"] - point["c5"])
    return [buckets[key] for key in sorted(buckets)]


@console_ajax(methods=["GET"])
def tenant_traffic(request, pk):
    """One client, with the per-minute series behind the headline figures."""
    tenant = _get_tenant(pk)
    minutes = _period(request)
    traffic.flush()

    policy = traffic.policy_for(tenant.pk)
    snapshots = traffic.snapshot([tenant.pk], minutes=minutes, history=traffic.MAX_MINUTES)
    snapshot = snapshots[tenant.pk]

    return {
        "tenant": traffic_json(tenant, snapshot, policy),
        # The full hour for one client, in the same shape the hero chart eats,
        # so the detail view draws with the same code rather than its own.
        "timeline": _timeline(snapshots, {tenant.pk}),
    }


def _parse_limits(payload) -> tuple[dict, object]:
    """Read the four limit boxes, treating blank as "no limit"."""
    limits: dict = {}
    errors: dict = {}

    for name in services.RATE_FIELDS:
        raw = payload.get(name, "")
        if raw in (None, "", "null"):
            limits[name] = None
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            errors[name] = [_("رقم غير صحيح")]
            continue
        if value < 0 or value > MAX_LIMIT:
            errors[name] = [_("قيمة خارج المدى المسموح")]
            continue
        # Zero and blank both mean "no ceiling here". Burst has no separate
        # unlimited state, so a zero there is simply no burst.
        limits[name] = value if value or name == "burst" else None

    if limits.get("burst") and not limits.get("max_rps"):
        errors["burst"] = [_("سماح الذروة يحتاج حدًا لكل ثانية أولًا.")]

    if errors:
        return {}, fail("ERR_VALIDATION", _("بيانات غير صحيحة"), status=400, field_errors=errors)
    return limits, None


@console_ajax(methods=["POST"])
def tenant_traffic_policy(request, pk):
    """Block, unblock, or set this client's limits — each one audited.

    One endpoint with an explicit ``action`` rather than three, so the console
    cannot half-apply a change: an operator who sets limits *and* lifts a block
    in one dialog gets one request, one transaction and one answer.
    """
    tenant = _get_tenant(pk)
    action = request.json.get("action")
    reason = (request.json.get("reason") or "").strip()

    if action == "block":
        if len(reason) < 4:
            return fail(
                "ERR_VALIDATION",
                _("اكتب سببًا واضحًا."),
                status=400,
                field_errors={"reason": [_("السبب مطلوب")]},
            )
        services.block_traffic(tenant, reason=reason, actor=request.user)
        message = _("تم إيقاف الطلبات لهذا العميل.")

    elif action == "unblock":
        services.unblock_traffic(tenant, reason=reason, actor=request.user)
        message = _("عادت الطلبات للعمل.")

    elif action == "limits":
        limits, error = _parse_limits(request.json)
        if error is not None:
            return error
        try:
            services.set_rate_limits(tenant, actor=request.user, reason=reason, **limits)
        except DjangoValidationError as exc:
            return fail(
                "ERR_VALIDATION",
                _("بيانات غير صحيحة"),
                status=400,
                field_errors={
                    key: [str(m) for m in value] for key, value in exc.message_dict.items()
                },
            )
        message = _("تم حفظ الحدود.") if any(limits.values()) else _("رُفعت كل الحدود.")

    else:
        return fail("ERR_VALIDATION", _("إجراء غير معروف"), status=400)

    policy = traffic.policy_for(tenant.pk)
    snapshot = traffic.snapshot([tenant.pk]).get(tenant.pk)
    return {"tenant": traffic_json(tenant, snapshot, policy), "message": message}


def plan_json(plan) -> dict:
    return {
        "id": plan.pk,
        "slug": plan.slug,
        "name": plan.name,
        "is_active": plan.is_active,
        "currency": plan.currency,
        "monthly_price": str(plan.monthly_price) if plan.monthly_price is not None else None,
        "yearly_price": str(plan.yearly_price) if plan.yearly_price is not None else None,
        "effective_monthly": (
            str(plan.effective_monthly) if plan.effective_monthly is not None else None
        ),
        "effective_yearly": (
            str(plan.effective_yearly) if plan.effective_yearly is not None else None
        ),
        "discount_percent": str(plan.discount_percent),
        "discount_live": plan.discount_is_live,
        "yearly_saving_percent": plan.yearly_saving_percent,
        "features": len(plan.feature_keys),
    }


@console_ajax(methods=["POST"])
def plan_active(request, pk):
    """Retire or restore a plan without touching the clients already on it.

    Deactivating hides a plan from the wizard and the plan picker; it does not
    move, downgrade or warn a single existing client. Changing what someone
    already pays for is a conversation, not a side effect of tidying a price
    list.
    """
    plan = Plan.objects.filter(pk=pk).first()
    if plan is None:
        raise Http404("no such plan")

    active = bool(request.json.get("is_active"))
    if not active and not Plan.objects.filter(is_active=True).exclude(pk=plan.pk).exists():
        return fail(
            "ERR_LAST_PLAN",
            _("لا يمكن إيقاف آخر باقة متاحة — لن تتمكن من إنشاء عملاء جدد."),
            status=409,
        )

    services.set_plan_active(plan, active, actor=request.user)
    return {
        "plan": plan_json(plan),
        "message": _("الباقة متاحة الآن.") if active else _("أُوقفت الباقة للعملاء الجدد."),
    }


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
        return {"available": False, "reason": _("هذا المعرّف مستخدم بالفعل.")}

    host = services.host_for(slug, settings.TENANT_BASE_DOMAIN)
    if Domain.objects.filter(host=host).exists():
        reason = _("النطاق %(host)s مستخدم بالفعل.") % {"host": host}
        return {"available": False, "reason": reason}

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
