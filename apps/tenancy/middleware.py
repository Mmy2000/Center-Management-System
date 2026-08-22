"""Turning a ``Host:`` header into a tenant (docs/10 §N.3, TASK-092).

Three middlewares, at three deliberate positions in the stack:

  [3]  TenantResolutionMiddleware    before AuthenticationMiddleware, because
                                     the user lookup itself is tenant-scoped
  [8]  TenantSessionGuardMiddleware  after it, once request.user exists
  [11] TenantStatusMiddleware        after messages, so the gate page can use them

The contextvar is always reset in ``finally`` — the same discipline
``AuditContextMiddleware`` already applies to its thread-local. A leaked value
means the next request served by that worker reads another center's data.
"""

import logging

from django.http import Http404
from django.shortcuts import render
from django.utils import translation

from .constants import TenantStatus
from .context import reset_tenant, set_tenant
from .resolution import normalize_host, resolve_host

logger = logging.getLogger("tenancy")


def _console_host(request=None) -> str:
    from django.conf import settings

    return normalize_host(getattr(settings, "CONSOLE_HOST", "") or "")


class TenantResolutionMiddleware:
    """Resolve the tenant, or 404.

    An unknown host gets a flat 404 — never a redirect to a default center and
    never a "no such center" page. Both would confirm which hostnames exist,
    which is the first step of enumerating your client list.
    """

    #: Paths that answer without a tenant. ``/healthz/`` is a *process* check
    #: (docs/07 §L.6) and must not touch the database — resolving a tenant in
    #: front of it would make a database outage look like a dead process, and
    #: an unknown host make a healthy one look dead. ``/readyz/`` deliberately
    #: is not here: it is the check that *should* fail when the DB is gone.
    TENANTLESS_PREFIXES = ("/healthz/", "/static/", "/media/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith(self.TENANTLESS_PREFIXES):
            request.tenant = None
            request.is_console = False
            return self.get_response(request)

        host = normalize_host(request.get_host())
        console = _console_host()

        if console and host == console:
            # The console has its own URLconf, so a tenant host cannot route to
            # it at all — no permission bug or proxy mistake can bridge them.
            from django.conf import settings

            request.tenant = None
            request.is_console = True
            request.urlconf = getattr(settings, "CONSOLE_URLCONF", "cms.urls_console")
            token = set_tenant(None)
            try:
                return self.get_response(request)
            finally:
                reset_tenant(token)

        tenant = resolve_host(host)
        if tenant is None:
            logger.warning("Unknown tenant host: %s", host)
            raise Http404("Unknown host")

        request.tenant = tenant
        request.is_console = False
        token = set_tenant(tenant)
        try:
            with translation.override(tenant.language or translation.get_language()):
                return self.get_response(request)
        finally:
            reset_tenant(token)


class TenantSessionGuardMiddleware:
    """A session may only be used on the host it belongs to.

    Session cookies are already per-subdomain (``SESSION_COOKIE_DOMAIN`` is
    deliberately unset — docs/10 §N.6), so this should never fire. It exists
    because "should never fire" is not a security control: a misconfigured
    cookie domain, a shared custom domain or a replayed cookie would otherwise
    hand one center a session belonging to another.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        tenant = getattr(request, "tenant", None)

        if user is not None and user.is_authenticated:
            mismatch = self._mismatch(request, user, tenant)
            if mismatch:
                logger.warning(
                    "Session/tenant mismatch (%s): user=%s user_tenant=%s request_tenant=%s",
                    mismatch,
                    user.pk,
                    getattr(user, "tenant_id", None),
                    getattr(tenant, "pk", None),
                )
                self._flush(request)
        return self.get_response(request)

    def _mismatch(self, request, user, tenant) -> str | None:
        user_tenant_id = getattr(user, "tenant_id", None)

        if getattr(request, "is_console", False):
            # Only platform staff belong on the console host.
            if not getattr(user, "is_platform_staff", False):
                return "tenant user on console host"
            return None

        if tenant is None:
            return None

        if user_tenant_id is None:
            # Platform staff on a tenant host: allowed only while impersonating,
            # which TASK-115 writes into the session. Until then, refused.
            if self._impersonation_matches(request, tenant):
                return None
            return "platform user on tenant host"

        if user_tenant_id != tenant.pk:
            return "cross-tenant session"

        return None

    def _impersonation_matches(self, request, tenant) -> bool:
        record = request.session.get("impersonation") or {}
        return bool(record) and record.get("tenant_id") == tenant.pk

    def _flush(self, request):
        from django.contrib.auth import logout

        logout(request)


class TenantStatusMiddleware:
    """A suspended center gets a door, not a shredder.

    Blocks every URL except the few needed to log out, check health, or read the
    gate page itself. Nothing is deleted and nothing is hidden — resuming the
    tenant restores it byte-for-byte.
    """

    #: ``namespace:url_name`` values that stay reachable while suspended.
    EXEMPT_NAMES = frozenset(
        {
            "core:healthz",
            "core:readyz",
            "accounts:logout",
            "tenancy:gate",
        }
    )
    EXEMPT_PREFIXES = ("/static/", "/media/", "/i18n/", "/jsi18n/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        tenant = getattr(request, "tenant", None)
        if tenant is None or tenant.is_operational:
            return None
        if request.path.startswith(self.EXEMPT_PREFIXES):
            return None

        match = request.resolver_match
        name = f"{match.namespace}:{match.url_name}" if match and match.namespace else None
        if name in self.EXEMPT_NAMES:
            return None

        return self.gate(request, tenant)

    @staticmethod
    def gate(request, tenant):
        from django.contrib.auth import logout

        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            logout(request)
        return render(
            request,
            "tenancy/gate.html",
            {
                "tenant": tenant,
                "is_archived": tenant.status == TenantStatus.ARCHIVED,
            },
            status=403,
        )
