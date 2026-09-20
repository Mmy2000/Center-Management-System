"""Host → tenant, in zero queries once warm (docs/10 §N.3, TASK-092).

This runs on **every** request, including the scan path, whose budget is six
queries and 120 ms p95 (docs/README principle 6). An uncached lookup would spend
one of those six on plumbing, so the mapping is cached by host — and, just as
importantly, so is the *negative* answer: an unknown-host flood must not turn
into a database query per packet.
"""

import logging

from django.core.cache import cache

logger = logging.getLogger("tenancy")

CACHE_PREFIX = "tenancy:host:"
CACHE_TTL = 300
#: Unknown hosts are cached for a shorter window: a domain that was just added
#: in the console should start working within a minute, not five.
NEGATIVE_CACHE_TTL = 60

_MISS = "__miss__"


def normalize_host(raw_host: str) -> str:
    """Reduce a ``Host:`` header to the form stored in ``Domain.host``.

    Strips the port, lowercases, drops a trailing dot, and handles the IPv6
    literal form (``[::1]:8000``) that would otherwise lose its brackets.
    """
    host = (raw_host or "").strip().lower().rstrip(".")
    if host.startswith("["):  # IPv6 literal
        closing = host.find("]")
        if closing != -1:
            return host[: closing + 1]
        return host
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def cache_key(host: str) -> str:
    return f"{CACHE_PREFIX}{host}"


def resolve_host(host: str):
    """Return the ``Tenant`` for ``host``, or ``None``.

    The whole tenant instance is cached, not just its id, so a warm request
    costs **zero** queries — principle 6 says the scan path gets six, and none
    of them should go on plumbing. Staleness is handled by invalidation rather
    than by a short TTL: saving a Tenant or a Domain drops the key (see
    ``tenancy.signals``), so a suspension bites on the very next request.

    Unpickling is defensive: a cached instance written before a schema change
    would otherwise raise on every request until the TTL expired. A bad entry is
    simply dropped and re-read.
    """
    from .models import Domain

    key = cache_key(host)
    try:
        cached = cache.get(key)
    except Exception:  # unpicklable payload from an older deploy
        logger.warning("Dropping unreadable tenancy cache entry for %s", host)
        cache.delete(key)
        cached = None

    if cached == _MISS:
        return None
    if cached is not None:
        return cached

    domain = (
        Domain.objects
        # `tenant__rate_policy` rides along deliberately: TenantTrafficMiddleware
        # needs it on every single request, and selecting it here makes that
        # cost zero rather than one query or one more cache key (TASK-122).
        # Its writes invalidate this same cache, so it cannot go stale on its
        # own — see tenancy.signals.
        .select_related("tenant", "tenant__plan", "tenant__rate_policy")
        .filter(host=host)
        .first()
    )
    if domain is None:
        cache.set(key, _MISS, NEGATIVE_CACHE_TTL)
        return None

    cache.set(key, domain.tenant, CACHE_TTL)
    return domain.tenant


def invalidate_host(host: str) -> None:
    cache.delete(cache_key(host))


def invalidate_tenant(tenant) -> None:
    """Drop every host key that points at ``tenant``.

    Called when a tenant is suspended or its plan changes — the host cache
    stores only an id, but the *tenant* row behind it must be re-read, and the
    cheapest correct thing is to drop the keys.
    """
    from .models import Domain

    hosts = Domain.objects.filter(tenant=tenant).values_list("host", flat=True)
    cache.delete_many([cache_key(host) for host in hosts])
