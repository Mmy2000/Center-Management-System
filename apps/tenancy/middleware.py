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


def _console_path_match(request) -> bool:
    """Whether this request is for a path-mounted console (single-host hosting)."""
    from django.conf import settings

    prefix = (getattr(settings, "CONSOLE_PATH_PREFIX", "") or "").strip("/")
    if not prefix:
        return False
    return request.path == f"/{prefix}" or request.path.startswith(f"/{prefix}/")


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

        from django.conf import settings

        host = normalize_host(request.get_host())
        console = _console_host()
        via_path = _console_path_match(request)

        if (console and host == console) or via_path:
            # The console has its own URLconf, so a tenant host cannot route to
            # it at all — no permission bug or proxy mistake can bridge them.
            # Under CONSOLE_PATH_PREFIX that structural wall is gone by
            # definition (same origin); `platform_staff_required` is then the
            # only wall, which is why the prefix is opt-in and documented as
            # single-host hosting only.
            request.tenant = None
            request.is_console = True
            request.console_via_path = via_path
            request.urlconf = (
                getattr(settings, "CONSOLE_PATH_URLCONF", "cms.urls_console_path")
                if via_path
                else getattr(settings, "CONSOLE_URLCONF", "cms.urls_console")
            )
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
            return self.get_response(request)
        finally:
            reset_tenant(token)


class LanguageMiddleware:
    """Decide the page's language, after ``LocaleMiddleware`` has had its say.

    Placement is the whole point. ``LocaleMiddleware`` calls
    ``translation.activate()`` inside its own ``__call__``, so anything decided
    *before* it is silently overwritten — which is exactly what happened to the
    first attempt at this, in both branches, without failing a single test.

    Two rules, in order:

    * **The console is pinned.** Its templates are Arabic literals rather than
      translation calls, so a browser set to English produced Arabic headings
      beside English form labels — a screen that looks broken because it is.
    * **A center's own default applies only when the user has not chosen.**
      The language switcher writes a cookie; overriding that would break the
      bilingual UI the product ships. So ``tenant.language`` is a fallback, not
      a command — which is what makes the field mean something at last.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = self._language_for(request)
        if language is None:
            return self.get_response(request)
        with translation.override(language):
            request.LANGUAGE_CODE = translation.get_language()
            return self.get_response(request)

    @staticmethod
    def _language_for(request) -> str | None:
        from django.conf import settings

        if getattr(request, "is_console", False):
            return getattr(settings, "CONSOLE_LANGUAGE", "ar") or None

        tenant = getattr(request, "tenant", None)
        if tenant is None or not tenant.language:
            return None

        # Whatever the user picked wins. Django 4.0 dropped session-stored
        # languages, so the cookie the switcher writes is the only place to
        # look — and `set_language` writes it on every switch.
        if request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME):
            return None

        return tenant.language


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
                # Under a path-mounted console the two share an origin, so a
                # center admin who mistypes a URL is not a cross-host session —
                # they are one of their own users on their own site. Let the
                # view 404 them instead of signing them out of their center.
                if getattr(request, "console_via_path", False):
                    return None
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
        from . import impersonation

        record = impersonation.current(request)
        return bool(record) and record.get("tenant_id") == tenant.pk

    def _flush(self, request):
        from django.contrib.auth import logout

        logout(request)


class ImpersonationGuardMiddleware:
    """Keep an operator inside a client's account within their bounds (TASK-115).

    Placed after the session guard, which is what let them in at all. This one
    decides what they may *do*: read-only unless the entry said otherwise, and
    never money or credentials in either mode (see
    :mod:`apps.tenancy.impersonation` for why).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from . import impersonation

        record = impersonation.current(request)
        request.impersonation = record
        if not record:
            return None

        reason = impersonation.blocks(request, record)
        if reason is None:
            return None

        logger.warning(
            "Impersonation refused (%s): operator=%s tenant=%s path=%s",
            reason,
            record.get("operator_id"),
            record.get("tenant_slug"),
            request.path,
        )
        return self._refuse(request, reason)

    @staticmethod
    def _refuse(request, reason):
        from django.utils.translation import gettext as _

        message = (
            _("لا يمكن تنفيذ هذا الإجراء أثناء الدخول نيابةً عن العميل.")
            if reason == "forbidden endpoint"
            else _("جلسة قراءة فقط — لا يمكن التعديل.")
        )
        if request.headers.get("x-requested-with") == "XMLHttpRequest" or request.path.startswith(
            "/api/"
        ):
            from apps.core.http import fail

            return fail("ERR_IMPERSONATION_READ_ONLY", message, status=403)

        from django.contrib import messages
        from django.shortcuts import redirect

        messages.error(request, message)
        return redirect(request.META.get("HTTP_REFERER") or "/")


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
