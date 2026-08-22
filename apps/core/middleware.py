from django.shortcuts import redirect
from django.urls import reverse

from . import audit


class AuditContextMiddleware:
    """Expose the current user/IP to the audit service via a thread-local.

    The context is always cleared in ``finally`` so nothing can leak between
    requests served by the same worker thread.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        audit.set_context(
            user=getattr(request, "user", None),
            ip=audit.client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
        try:
            return self.get_response(request)
        finally:
            audit.clear_context()


class ForcePasswordChangeMiddleware:
    """Users flagged ``must_change_password`` cannot use the app until they do."""

    EXEMPT_NAMES = {
        "accounts:password_change",
        "accounts:logout",
        "accounts:login",
        "core:healthz",
        "core:readyz",
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated and getattr(user, "must_change_password", False)):
            return None
        if request.path.startswith(("/static/", "/media/", "/admin/")):
            return None
        match = request.resolver_match
        name = f"{match.namespace}:{match.url_name}" if match and match.namespace else None
        if name in self.EXEMPT_NAMES:
            return None
        return redirect(reverse("accounts:password_change"))
