"""AJAX sign-in.

The page still posts normally when JavaScript is unavailable — this endpoint
exists so the button can show a spinner and errors can appear inline instead of
through a full reload. Both paths share the same throttle bucket and the same
redirect rules, so neither can be used to bypass the other.
"""

from django.contrib.auth import login as auth_login
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _

from apps.core import ratelimit
from apps.core.audit import client_ip
from apps.core.http import DomainError, ajax

from .forms import CenterAuthenticationForm

LOGIN_LIMIT = 5
LOGIN_WINDOW = 60


def safe_redirect(request, candidate: str) -> str:
    """Never bounce a signed-in user to another host (open-redirect guard)."""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return reverse("dashboard:home")


@ajax(methods=["POST"], login_required=False)
def login_ajax(request):
    identity = client_ip(request) or "unknown"
    ratelimit.check("login", identity, limit=LOGIN_LIMIT, window=LOGIN_WINDOW)

    form = CenterAuthenticationForm(request, data=request.json)
    if not form.is_valid():
        # One message for every failure — wrong password, unknown user, or a
        # disabled account. Django's own wording differs between them, which
        # would tell an attacker which usernames exist.
        raise DomainError(
            "ERR_INVALID_CREDENTIALS",
            _("بيانات دخول غير صحيحة"),
            status=401,
            field_errors={
                field: [str(m) for m in messages] for field, messages in form.errors.items()
                if field != "__all__"
            },
        )

    user = form.get_user()
    auth_login(request, user)

    if user.must_change_password:
        target = reverse("accounts:password_change")
    else:
        target = safe_redirect(request, request.json.get("next", ""))

    return {
        "redirect": target,
        "user": {"name": user.display_name, "role": user.get_role_display()},
        "code": "OK_SIGNED_IN",
    }
