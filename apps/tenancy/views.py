"""The gate a suspended center sees (TASK-092)."""

from django.http import JsonResponse
from django.views.decorators.cache import never_cache

from .middleware import TenantStatusMiddleware


@never_cache
def gate(request):
    """Reachable while suspended so the page can render its own assets."""
    tenant = getattr(request, "tenant", None)
    if tenant is None or tenant.is_operational:
        from django.shortcuts import redirect

        return redirect("dashboard:home")
    return TenantStatusMiddleware.gate(request, tenant)


@never_cache
def tls_check(request):
    """Told Caddy whether to issue a certificate for a hostname (TASK-121).

    Stubbed here so the URL exists from the start; TASK-121 wires the proxy to
    it and adds the rate limit.
    """
    from .resolution import normalize_host, resolve_host

    host = normalize_host(request.GET.get("domain", ""))
    tenant = resolve_host(host) if host else None
    if tenant is None or not tenant.is_operational:
        return JsonResponse({"ok": False}, status=404)
    return JsonResponse({"ok": True})
