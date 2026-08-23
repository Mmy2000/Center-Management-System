"""Bounds on entering a client's account (docs/10 §N.10, TASK-115).

This is the feature that costs you a client's trust if it is loose, so every
bound is explicit rather than assumed:

* **Read-only by default.** Writing needs a second, separately-audited toggle
  and a typed reason.
* **Money and deletion are refused in both modes.** There is no operator reason
  good enough to collect a payment or issue a refund as somebody else, and if
  there were, the audit trail would still say the center's cashier did it.
* **Hard expiry.** Thirty minutes, checked on every request, not just at entry.
* **No credential changes.** An operator inside a client's account cannot change
  a password or create a user — those are how a temporary visit becomes
  permanent access.
* **Visible.** A red banner on every page, naming the operator, with a way out.

The center's own ``AuditLog`` also records the operator as acting *on behalf of*
the client, so their audit trail never silently attributes your action to their
staff.
"""

from django.utils import timezone
from django.utils.dateparse import parse_datetime

SESSION_KEY = "impersonation"

#: URL names an impersonating operator may never POST to, in either mode.
#: Matched as ``namespace:url_name``.
FORBIDDEN_NAMES = frozenset(
    {
        # money in, money out
        "payments_api:payments",
        "payments_api:refund",
        "payments_api:waive",
        "payments_api:cancel",
        "payments_api:generate",
        # credentials — how a visit becomes permanent access
        "accounts_api:users",
        "accounts_api:user_detail",
        "accounts:password_change",
    }
)

#: HTTP methods that change something.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def current(request) -> dict | None:
    """The active impersonation record, or ``None``. Expired records are dropped."""
    record = request.session.get(SESSION_KEY)
    if not record:
        return None

    expires = parse_datetime(record.get("expires_at") or "")
    if expires is None or expires <= timezone.now():
        request.session.pop(SESSION_KEY, None)
        request.session.modified = True
        return None
    return record


def is_read_only(record: dict | None) -> bool:
    return bool(record) and record.get("mode") != "write"


def blocks(request, record: dict) -> str | None:
    """Why this request is refused, or ``None`` if it may proceed."""
    if request.method not in UNSAFE_METHODS:
        return None

    match = request.resolver_match
    name = f"{match.namespace}:{match.url_name}" if match and match.namespace else None
    if name in FORBIDDEN_NAMES:
        return "forbidden endpoint"

    if is_read_only(record):
        return "read-only session"
    return None
