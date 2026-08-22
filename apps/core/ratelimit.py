"""Small cache-backed rate limiter (docs/06 §I.2).

Deliberately dependency-free: it uses whatever cache backend is configured
(LocMem in dev, Redis in production). Counters are approximate by design —
this protects against runaway clients and password guessing, not against a
determined distributed attacker, which is nginx's job.
"""

import time
from functools import wraps

from django.core.cache import cache
from django.utils.translation import gettext as _

from apps.tenancy.context import current_tenant_id

from .audit import client_ip
from .http import DomainError


def hit(scope: str, identity: str, *, limit: int, window: int) -> bool:
    """Register one hit. Returns True when the caller is still within budget."""
    bucket = int(time.time() // window)
    # Namespaced by tenant: one center's traffic must never spend another
    # center's budget, and a shared bucket would let either do it accidentally
    # or on purpose (docs/10 §N.7).
    key = f"rl:{current_tenant_id() or 0}:{scope}:{identity}:{bucket}"
    try:
        added = cache.add(key, 1, window + 1)
        count = 1 if added else cache.incr(key)
    except ValueError:  # key expired between add() and incr()
        cache.set(key, 1, window + 1)
        count = 1
    return count <= limit


def check(scope: str, identity: str, *, limit: int, window: int) -> None:
    """Raise :class:`DomainError` (429) when the budget is exhausted."""
    if not hit(scope, identity, limit=limit, window=window):
        raise DomainError(
            "ERR_RATE_LIMITED",
            _("عدد محاولات كبير، برجاء المحاولة بعد قليل"),
            status=429,
        )


def rate_limit(scope: str, *, limit: int, window: int, by="ip"):
    """Decorator form for plain (non-AJAX) views."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            identity = (
                client_ip(request) if by == "ip" else str(getattr(request.user, "pk", "anon"))
            )
            check(scope, identity or "unknown", limit=limit, window=window)
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
