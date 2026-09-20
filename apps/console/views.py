"""The platform console (docs/10 §N.10, TASK-111 → 118).

Thin views over :mod:`apps.console.services`. Everything consequential —
provisioning, suspending, toggling a feature — lives in the service so the web
UI and the management commands cannot drift apart.
"""

import json

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from apps.core.http import fail, ok
from apps.tenancy import quota
from apps.tenancy.constants import (
    OPERATIONAL_STATUSES,
    FeatureState,
    TenantStatus,
    TrafficMode,
)
from apps.tenancy.features import grouped_specs
from apps.tenancy.models import Plan, PlatformAuditLog, Tenant, TenantFeature
from apps.tenancy.resolver import enabled_features

from . import forms, services
from .decorators import platform_staff_required

PAGE_SIZE = 25


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #


@never_cache
def console_login(request):
    """Sign in as platform staff.

    Deliberately separate from the tenant login: this host has no tenant, and
    the backend therefore scopes the lookup to users with ``tenant IS NULL``.
    A center's admin cannot sign in here even with the right password.
    """
    if not getattr(request, "is_console", False):
        raise Http404("not the console host")

    wants_json = request.headers.get("x-requested-with") == "XMLHttpRequest"
    error = None

    if request.method == "POST":
        credentials = _credentials(request)
        user = authenticate(request, **credentials)

        # `is_platform_staff` is checked here as well as in the backend: a
        # center's admin is already invisible to this host's lookup, but the
        # answer to "may this account use the console" belongs at the door too.
        if user is not None and user.is_platform_staff:
            login(request, user)
            destination = request.GET.get("next") or reverse("console:dashboard")
            if wants_json:
                return ok({"next": destination})
            return redirect(destination)

        # One message for every failure — unknown user, wrong password, a
        # center's account. Distinguishing them would tell an attacker which
        # usernames exist on the platform.
        error = _("بيانات الدخول غير صحيحة.")
        if wants_json:
            return fail("ERR_AUTH_FAILED", error, status=401)

    return render(request, "console/login.html", {"error": error}, status=401 if error else 200)


def _credentials(request) -> dict:
    """Read the login from a JSON body or a plain form post, whichever came."""
    if (request.content_type or "").startswith("application/json") and request.body:
        import json

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = {}
    else:
        payload = request.POST
    return {
        "username": (payload.get("username") or "").strip(),
        "password": payload.get("password") or "",
    }


@never_cache
def console_logout(request):
    logout(request)
    return redirect(reverse("console:login"))


# --------------------------------------------------------------------------- #
# Dashboard (TASK-117)
# --------------------------------------------------------------------------- #


@platform_staff_required
def dashboard(request):
    now = timezone.now()
    soon = now + timezone.timedelta(days=30)

    tenants = Tenant.objects.select_related("plan", "usage")
    by_status = dict(
        tenants.values_list("status").annotate(n=Count("pk")).values_list("status", "n")
    )

    totals = Tenant.objects.aggregate(
        students=Sum("usage__students"),
        users=Sum("usage__users"),
        scans=Sum("usage__scans_30d"),
        collected=Sum("usage__payments_30d_total"),
    )

    expiring = tenants.filter(
        status__in=OPERATIONAL_STATUSES,
        expires_at__isnull=False,
        expires_at__lte=soon,
    ).order_by("expires_at")[:10]

    trials_ending = tenants.filter(
        status=TenantStatus.TRIAL, trial_ends_at__isnull=False, trial_ends_at__lte=soon
    ).order_by("trial_ends_at")[:10]

    over_quota = [t for t in tenants if _quota_pressure(t) >= 90]

    return render(
        request,
        "console/dashboard.html",
        {
            "nav": "dashboard",
            # Built here, not in the template: Django templates cannot index a
            # dict by a loop variable, and a custom filter for one screen is
            # more machinery than a list comprehension.
            "status_rows": [
                (value, label, by_status.get(value, 0)) for value, label in TenantStatus.choices
            ],
            "total_tenants": sum(by_status.values()),
            "totals": totals,
            "expiring": expiring,
            "trials_ending": trials_ending,
            "over_quota": over_quota[:10],
            "new_this_month": tenants.filter(created_at__gte=now.replace(day=1)).count(),
            "recent_audit": PlatformAuditLog.objects.select_related("actor", "tenant")[:20],
        },
    )


# --------------------------------------------------------------------------- #
# Traffic monitoring (TASK-122)
# --------------------------------------------------------------------------- #


@platform_staff_required
@never_cache
def traffic(request):
    """The shell for the live monitoring table.

    Deliberately a shell. Every figure on this page comes out of the cache and
    changes by the second, so rendering any of it server-side would mean
    shipping a number that is already wrong and then immediately replacing it.
    The page therefore loads empty and ``api_traffic`` fills it — the same
    pattern the client list already uses, for a weaker reason.

    The one thing rendered here is the warning about a per-process cache,
    because that is a fact about the deployment rather than about the traffic,
    and an operator reading wrong numbers should be told why before they act
    on them.
    """
    from apps.tenancy import traffic as traffic_metrics

    from .api import PERIODS

    return render(
        request,
        "console/traffic.html",
        {
            "nav": "traffic",
            "lifecycle_choices": TenantStatus.choices,
            "mode_choices": TrafficMode.choices,
            "periods": PERIODS,
            "cache_shared": traffic_metrics.cache_is_shared(),
        },
    )


def _quota_pressure(tenant) -> int:
    """The highest percentage of any limit this client is using.

    Read from the cached usage row, never live: this runs once per tenant on a
    list page, and a hundred live counts would make the console the slowest
    page in the product.
    """
    usage = getattr(tenant, "usage", None)
    if usage is None:
        return 0
    worst = 0
    for resource, (_app, _model, field) in quota.RESOURCES.items():
        cap = getattr(tenant.plan, f"max_{resource}", None)
        if not cap:
            continue
        worst = max(worst, int(getattr(usage, field, 0) * 100 / cap))
    return worst


# --------------------------------------------------------------------------- #
# Tenants (TASK-112)
# --------------------------------------------------------------------------- #


@platform_staff_required
def tenant_list(request):
    """One query plus pagination, whatever the client count.

    The counts come from ``TenantUsage`` (docs/10 §N.2) precisely so this page
    does not become N x 6 aggregates over every center's data.
    """
    tenants = Tenant.objects.select_related("plan", "usage").prefetch_related("domains")

    status = request.GET.get("status")
    if status:
        tenants = tenants.filter(status=status)
    plan = request.GET.get("plan")
    if plan:
        tenants = tenants.filter(plan__slug=plan)
    if request.GET.get("expiring"):
        tenants = tenants.filter(
            expires_at__isnull=False, expires_at__lte=timezone.now() + timezone.timedelta(days=30)
        )
    query = (request.GET.get("q") or "").strip()
    if query:
        tenants = tenants.filter(
            Q(name__icontains=query)
            | Q(slug__icontains=query)
            | Q(owner_email__icontains=query)
            | Q(domains__host__icontains=query)
        ).distinct()

    rows = list(tenants.order_by("name"))
    if request.GET.get("over_quota"):
        rows = [t for t in rows if _quota_pressure(t) >= 90]

    page = Paginator(rows, PAGE_SIZE).get_page(request.GET.get("page"))
    for tenant in page:
        tenant.quota_pressure = _quota_pressure(tenant)

    return render(
        request,
        "console/tenant_list.html",
        {
            "nav": "tenants",
            "page": page,
            "plans": Plan.objects.all(),
            "status_choices": TenantStatus.choices,
            "filters": request.GET,
        },
    )


@platform_staff_required
def tenant_detail(request, pk):
    tenant = get_object_or_404(
        Tenant.objects.select_related("plan", "usage").prefetch_related("domains"), pk=pk
    )
    from .api import _usage_json

    return render(
        request,
        "console/tenant_detail.html",
        {
            "nav": "tenants",
            "tenant": tenant,
            "usage": getattr(tenant, "usage", None),
            # Rendered into the page rather than fetched on load: the numbers
            # are already in hand, and a table that flashes empty on every visit
            # is a worse experience than one that is simply correct.
            "usage_json": json.dumps(_usage_json(tenant), ensure_ascii=False),
            "features": enabled_features(tenant),
            "timeline": PlatformAuditLog.objects.filter(tenant=tenant).select_related("actor")[:25],
            "plans": Plan.objects.all(),
        },
    )


@platform_staff_required
@require_POST
def tenant_usage_refresh(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    services.refresh_usage(tenant)
    messages.success(request, _("تم تحديث الأرقام."))
    return redirect(reverse("console:tenant_detail", args=[tenant.pk]))


# --------------------------------------------------------------------------- #
# Provisioning (TASK-113)
# --------------------------------------------------------------------------- #


@platform_staff_required
def tenant_new(request):
    from django.conf import settings as django_settings

    form = forms.TenantCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        result = services.provision_tenant(
            slug=data["slug"],
            name=data["name"],
            plan=data["plan"],
            base_domain=django_settings.TENANT_BASE_DOMAIN,
            owner_username=data["owner_username"],
            owner_name=data["owner_name"],
            owner_email=data["owner_email"],
            owner_phone=data["owner_phone"],
            trial_days=data["trial_days"] or None,
            billing_cycle=data["billing_cycle"],
            seed_academics=data["seed_academics"],
            demo=data["demo"],
            actor=request.user,
        )
        # Shown exactly once, never stored and never logged.
        return render(
            request,
            "console/tenant_created.html",
            {
                "tenant": result["tenant"],
                "owner": result["owner"],
                "password": result["password"],
            },
        )

    return render(
        request,
        "console/tenant_new.html",
        {"nav": "tenants", "form": form, "base_domain": django_settings.TENANT_BASE_DOMAIN},
    )


# --------------------------------------------------------------------------- #
# Lifecycle and plan (TASK-114)
# --------------------------------------------------------------------------- #


@platform_staff_required
@require_POST
def tenant_status(request, pk):
    tenant = get_object_or_404(Tenant.objects.select_related("plan"), pk=pk)
    action = request.POST.get("action")
    form = forms.ReasonForm(request.POST)

    if action == "resume":
        services.resume(tenant, reason=request.POST.get("reason", ""), actor=request.user)
        messages.success(request, _("تم إعادة تفعيل العميل."))
    elif form.is_valid():
        reason = form.cleaned_data["reason"]
        if action == "suspend":
            services.suspend(tenant, reason=reason, actor=request.user)
            messages.warning(request, _("تم إيقاف العميل. البيانات كما هي."))
        elif action == "archive":
            services.archive(tenant, reason=reason, actor=request.user)
            messages.warning(request, _("تمت أرشفة العميل."))
        else:
            messages.error(request, _("إجراء غير معروف."))
    else:
        messages.error(request, _("السبب مطلوب."))

    return redirect(reverse("console:tenant_detail", args=[tenant.pk]))


@platform_staff_required
@require_POST
def tenant_plan(request, pk):
    tenant = get_object_or_404(Tenant.objects.select_related("plan"), pk=pk)
    form = forms.PlanChangeForm(request.POST)
    if form.is_valid():
        plan = form.cleaned_data["plan"]
        for warning in services.plan_change_warnings(tenant, plan):
            messages.warning(request, warning)
        services.change_plan(tenant, plan, actor=request.user, reason=form.cleaned_data["reason"])
        quota.invalidate(tenant)
        messages.success(request, _("تم تغيير الباقة."))
    else:
        messages.error(request, _("باقة غير صحيحة."))
    return redirect(reverse("console:tenant_detail", args=[tenant.pk]))


@platform_staff_required
def tenant_features(request, pk):
    """Per-feature tri-state, showing plan default and effective value together.

    Nobody should have to reason about precedence in their head, so all three
    are on screen: what the plan says, what the override says, and what the
    center actually gets.
    """
    tenant = get_object_or_404(Tenant.objects.select_related("plan"), pk=pk)
    overrides = dict(
        TenantFeature.objects.filter(tenant=tenant).values_list("feature_key", "state")
    )
    effective = enabled_features(tenant)
    plan_keys = tenant.plan.feature_keys

    groups = []
    for group, label, specs in grouped_specs():
        rows = []
        for spec in specs:
            rows.append(
                {
                    "spec": spec,
                    "state": overrides.get(spec.key, FeatureState.INHERIT),
                    "plan_default": spec.key in plan_keys if plan_keys else spec.default,
                    "effective": spec.key in effective,
                }
            )
        groups.append({"group": group, "label": label, "rows": rows})

    return render(
        request,
        "console/tenant_features.html",
        {
            "nav": "tenants",
            "tenant": tenant,
            "groups": groups,
            "states": FeatureState.choices,
        },
    )


@platform_staff_required
@require_POST
def tenant_feature_set(request, pk):
    """AJAX: move one toggle. Returns the recomputed effective set."""
    from apps.core.http import DomainError, fail, ok

    tenant = get_object_or_404(Tenant.objects.select_related("plan"), pk=pk)
    form = forms.FeatureToggleForm(request.POST)
    if not form.is_valid():
        return fail("ERR_VALIDATION", _("بيانات غير صحيحة"), status=400)

    try:
        services.set_feature(
            tenant,
            form.cleaned_data["feature_key"],
            form.cleaned_data["state"],
            actor=request.user,
            note=form.cleaned_data["note"],
        )
    except KeyError:
        return fail("ERR_NOT_FOUND", _("خاصية غير معروفة"), status=404)
    except DomainError as exc:
        return fail(exc.code, exc.message, status=exc.status, data=exc.data)

    return ok({"effective": sorted(enabled_features(tenant))})


# --------------------------------------------------------------------------- #
# Impersonation (TASK-115)
# --------------------------------------------------------------------------- #

#: How long an operator may stay inside a client's account.
IMPERSONATION_MINUTES = 30


@platform_staff_required
@require_POST
def tenant_enter(request, pk):
    """Enter a client's account to help them — read-only unless stated.

    The record is written into the session and read by
    ``TenantSessionGuardMiddleware``, which is what allows platform staff on a
    tenant host at all. Everything about it is bounded: a mode, a typed reason,
    a hard expiry, and an audit row at both ends.
    """
    tenant = get_object_or_404(Tenant.objects.select_related("plan"), pk=pk)
    form = forms.ImpersonationForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("السبب مطلوب للدخول نيابةً عن العميل."))
        return redirect(reverse("console:tenant_detail", args=[tenant.pk]))

    if not tenant.is_operational:
        messages.error(request, _("لا يمكن الدخول إلى حساب موقوف."))
        return redirect(reverse("console:tenant_detail", args=[tenant.pk]))

    expires = timezone.now() + timezone.timedelta(minutes=IMPERSONATION_MINUTES)
    request.session["impersonation"] = {
        "tenant_id": tenant.pk,
        "tenant_slug": tenant.slug,
        "operator_id": request.user.pk,
        "operator_name": request.user.display_name,
        "mode": form.cleaned_data["mode"],
        "reason": form.cleaned_data["reason"],
        "expires_at": expires.isoformat(),
        "started_at": timezone.now().isoformat(),
    }
    request.session.modified = True

    from apps.tenancy.constants import PlatformAction
    from apps.tenancy.platform_audit import record

    record(
        PlatformAction.IMPERSONATION_STARTED,
        tenant=tenant,
        actor=request.user,
        reason=form.cleaned_data["reason"],
        changes={"mode": form.cleaned_data["mode"], "expires_at": expires.isoformat()},
    )

    scheme = "https" if request.is_secure() else "http"
    return redirect(f"{scheme}://{tenant.primary_host}/")


@platform_staff_required
def tenant_leave(request):
    """End an impersonation session from the console side."""
    record_impersonation_end(request)
    messages.success(request, _("تم إنهاء الدخول نيابةً."))
    return redirect(reverse("console:tenant_list"))


def record_impersonation_end(request):
    """Close the audit entry. Safe to call when nothing is open."""
    record_data = request.session.pop("impersonation", None)
    if not record_data:
        return None

    from apps.tenancy.constants import PlatformAction
    from apps.tenancy.platform_audit import record

    started = record_data.get("started_at")
    duration = None
    if started:
        from django.utils.dateparse import parse_datetime

        begun = parse_datetime(started)
        if begun:
            duration = int((timezone.now() - begun).total_seconds())

    return record(
        PlatformAction.IMPERSONATION_ENDED,
        tenant=Tenant.objects.filter(pk=record_data.get("tenant_id")).first(),
        actor=request.user if request.user.is_authenticated else None,
        changes={"mode": record_data.get("mode"), "duration_seconds": duration},
    )


# --------------------------------------------------------------------------- #
# Export and deletion (TASK-116)
# --------------------------------------------------------------------------- #


@platform_staff_required
def tenant_export(request, pk):
    """Hand a client their data. Streams; never buffers a whole center."""
    from .export import export_response

    tenant = get_object_or_404(Tenant, pk=pk)
    return export_response(tenant, actor=request.user)


@platform_staff_required
@require_POST
def tenant_delete(request, pk):
    """Archive now, purge later. Never a synchronous delete from a click."""
    tenant = get_object_or_404(Tenant.objects.select_related("plan"), pk=pk)
    form = forms.TenantDeleteForm(request.POST, tenant=tenant)
    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, error[0])
        return redirect(reverse("console:tenant_detail", args=[tenant.pk]))

    services.archive(tenant, reason=form.cleaned_data["reason"], actor=request.user)
    messages.warning(
        request,
        _(
            "تمت أرشفة العميل. تُحذف البيانات نهائيًا بعد %(days)s يومًا "
            "(%(date)s) عبر أمر purge_tenants."
        )
        % {
            "days": tenant.plan.retention_days,
            "date": f"{tenant.purge_after:%Y-%m-%d}",
        },
    )
    return redirect(reverse("console:tenant_detail", args=[tenant.pk]))


# --------------------------------------------------------------------------- #
# Plans and audit (TASK-114/118)
# --------------------------------------------------------------------------- #


@platform_staff_required
def plan_list(request):
    plans = (
        Plan.objects.prefetch_related("features")
        .annotate(tenant_count=Count("tenants"))
        .order_by("-is_active", "sort_order", "slug")
    )
    return render(request, "console/plan_list.html", {"nav": "plans", "plans": plans})


@platform_staff_required
def plan_edit(request, pk=None):
    """One screen for creating and editing.

    The fields are identical either way, and two screens would drift — the new
    one would gain a field the edit one never got.
    """
    plan = get_object_or_404(Plan, pk=pk) if pk else None
    form = forms.PlanForm(request.POST or None, instance=plan)

    if request.method == "POST" and form.is_valid():
        saved = services.save_plan(form, actor=request.user)
        messages.success(request, _("تم حفظ الباقة."))
        return redirect(reverse("console:plan_list") + f"#plan-{saved.pk}")

    from apps.tenancy.features import IMPLEMENTED_METHOD_KEYS

    selected = set(form["features"].value() or [])
    groups = [
        {
            "label": label,
            "rows": [
                {
                    "spec": spec,
                    "checked": spec.key in selected,
                    # A method with no code behind it can be granted and priced,
                    # but the resolver will never turn it on — so the checkbox
                    # has to say so, or a tick reads as a promise.
                    "unbuilt": spec.is_method and spec.key not in IMPLEMENTED_METHOD_KEYS,
                }
                for spec in specs
            ],
        }
        for _group, label, specs in grouped_specs()
    ]

    return render(
        request,
        "console/plan_edit.html",
        {
            "nav": "plans",
            "form": form,
            "plan": plan,
            "tenant_count": plan.tenants.count() if plan else 0,
            "groups": groups,
            "feature_total": sum(len(g["rows"]) for g in groups),
        },
    )


@platform_staff_required
def audit(request):
    entries = PlatformAuditLog.objects.select_related("actor", "tenant")
    if request.GET.get("tenant"):
        entries = entries.filter(tenant_id=request.GET["tenant"])
    if request.GET.get("action"):
        entries = entries.filter(action=request.GET["action"])
    if request.GET.get("actor"):
        entries = entries.filter(actor_id=request.GET["actor"])

    from apps.tenancy.constants import PlatformAction

    return render(
        request,
        "console/audit.html",
        {
            "nav": "audit",
            "page": Paginator(entries, 50).get_page(request.GET.get("page")),
            "tenants": Tenant.objects.order_by("name"),
            "actions": PlatformAction.choices,
            "filters": request.GET,
        },
    )


@platform_staff_required
def health(request):
    """A JSON snapshot for monitoring: how many clients, in what state."""
    return JsonResponse(
        {
            "tenants": dict(
                Tenant.objects.values_list("status")
                .annotate(n=Count("pk"))
                .values_list("status", "n")
            ),
            "checked_at": timezone.now().isoformat(),
        }
    )
