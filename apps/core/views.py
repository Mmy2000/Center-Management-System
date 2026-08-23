from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

from apps.accounts.decorators import require_feature, require_perm

from .models import AuditLog
from .registry import settings_registry


def healthz(request):
    """Liveness: the process is up. No dependencies touched."""
    return JsonResponse({"ok": True, "status": "alive"})


def readyz(request):
    """Readiness: database and cache both answer."""
    checks = {}
    status = 200

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # pragma: no cover - exercised in failure drills
        checks["database"] = f"error: {exc.__class__.__name__}"
        status = 503

    try:
        cache.set("readyz", "1", 5)
        checks["cache"] = "ok" if cache.get("readyz") == "1" else "error: no roundtrip"
        if checks["cache"] != "ok":
            status = 503
    except Exception as exc:  # pragma: no cover
        checks["cache"] = f"error: {exc.__class__.__name__}"
        status = 503

    return JsonResponse({"ok": status == 200, "checks": checks}, status=status)


@require_perm("core.view_setting")
@require_feature("settings.editor")
def settings_page(request):
    """Policy editor (TASK-011)."""
    from .policies import GROUP_LABELS, GROUPS

    groups = [
        {
            "group": group,
            "label": GROUP_LABELS[group],
            "items": settings_registry.all_for_group(group),
        }
        for group in GROUPS
    ]
    return render(
        request,
        "core/settings.html",
        {"groups": groups, "can_edit": request.user.has_perm("core.change_setting")},
    )


@require_perm("core.view_auditlog")
@require_feature("audit.viewer")
def audit_log_page(request):
    entries = AuditLog.objects.select_related("actor", "content_type")[:200]
    return render(request, "core/audit_log.html", {"entries": entries})
