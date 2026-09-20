"""Per-tenant request metering and rate control (docs/10 §N.7).

This module runs on **every** request a center makes, so its cost is the first
thing about it that matters. Two separate mechanisms, with deliberately
different price tags:

* **Metering** (what the console shows) buffers counts in the worker's own
  memory and flushes one aggregate per tenant per wall-clock second. A request
  therefore pays one dictionary update — no cache round-trip, no query. The
  figures lag by up to a second and a partial second is lost when a worker
  exits; both are fine for a monitoring screen and neither can affect a limit
  decision, because limits never read these counters.

* **Enforcement** (what actually refuses traffic) cannot be buffered: two
  workers must share one budget or the limit is really N times what was typed.
  It uses atomic ``incr``, the same shape ``core.ratelimit`` uses. Crucially it
  costs **nothing at all** for a tenant with no limit configured — the common
  case — because a window with no limit is never counted.

Counters live in the cache; the policy rides the tenant object that
``resolution.resolve_host`` already caches per host. Nothing here writes to the
database on the request path, and nothing reads from it either once warm.

**The cache must be shared across workers for any of this to be accurate.**
Under the default ``LocMemCache`` each worker meters and limits in its own
private memory: the console (served by some other worker) sees a fraction of
the traffic, and a limit of 10/s becomes 10/s *per worker*. ``tenancy.W002``
says so at ``manage.py check`` time and the console page says so on screen.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass, field

from django.core.cache import cache

logger = logging.getLogger("tenancy")

PREFIX = "tf:"

#: Second slots feed the RPS figure and nothing else, so they need to outlive
#: only the window the console averages over, plus slack for a slow reader.
SECOND_TTL = 180
#: Minute slots are the backbone: RPM, status mix, latency and every period
#: total up to an hour are read from them.
MINUTE_TTL = 60 * 95
#: Hour slots carry the 24-hour totals, and only totals — keeping the status
#: mix at this resolution would double the write cost for a number nobody
#: reads at that range.
HOUR_TTL = 60 * 60 * 26
#: `last seen` is written on every flush, so it is the one key that is set
#: rather than incremented. It outlives the hour slots so an idle client still
#: shows *when* it went quiet rather than merely that it is quiet.
SEEN_TTL = 60 * 60 * 26

#: Seconds averaged into the headline RPS. One second alone is far too spiky to
#: read, and a minute hides exactly the burst this screen exists to catch.
RPS_WINDOW = 10

#: How many minute slots a single read may ask for. Sixty minutes across a
#: hundred clients is already 6 000 keys in one ``get_many``; beyond an hour the
#: hour slots answer the same question in a fortieth of the keys.
MAX_MINUTES = 60

#: The windows a policy can limit, longest last. Order is load-bearing: the
#: first breach wins and stops the walk, so a refused request never spends the
#: budget of a longer window it was never going to reach.
WINDOWS = (
    ("rps", 1, "max_rps"),
    ("rpm", 60, "max_rpm"),
    ("rph", 3600, "max_rph"),
)


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Policy:
    """What a tenant is allowed to spend, in a form safe to cache.

    A plain frozen dataclass rather than the model instance: this is read on
    every request and unpickling a model written before a schema change would
    raise on all of them at once (the lesson ``resolution.resolve_host``
    already learned).
    """

    blocked: bool = False
    blocked_reason: str = ""
    max_rps: int | None = None
    max_rpm: int | None = None
    max_rph: int | None = None
    burst: int = 0

    @property
    def has_limits(self) -> bool:
        return any(getattr(self, field_name) for _n, _s, field_name in WINDOWS)

    @property
    def mode(self) -> str:
        """``BLOCKED`` / ``LIMITED`` / ``ACTIVE``, derived rather than stored.

        Derived on purpose: a stored mode can disagree with the limits beside
        it, and then two screens tell an operator two different things about
        the same client.
        """
        from .constants import TrafficMode

        if self.blocked:
            return TrafficMode.BLOCKED
        return TrafficMode.LIMITED if self.has_limits else TrafficMode.ACTIVE

    def limit_for(self, window_seconds: int) -> int | None:
        """The ceiling for one window, burst included where it applies."""
        for _name, seconds, field_name in WINDOWS:
            if seconds != window_seconds:
                continue
            base = getattr(self, field_name)
            if base is None:
                return None
            # Burst is headroom on the per-second window only. Spread over a
            # minute it would be indistinguishable from simply raising the
            # per-minute limit, which is the control the operator already has.
            return base + self.burst if window_seconds == 1 else base
        return None


_UNSET = object()

#: A tenant with no row: unlimited and unblocked. Shared, immutable, and the
#: answer for the overwhelming majority of clients.
UNLIMITED = Policy()

#: The reverse accessor ``resolve_host`` selects along with the tenant. Named
#: once here because the fast path below reaches into the instance's relation
#: cache by that name.
RELATION = "rate_policy"


def policy_for(tenant) -> Policy:
    """This tenant's policy. Free on the hot path, one query anywhere else.

    Accepts a ``Tenant`` or a bare id. Given the instance the middleware
    already holds, this costs **nothing**: ``resolution.resolve_host`` selects
    the policy row alongside the tenant and caches the pair per host, so by the
    time the middleware asks, the answer is already in the object's relation
    cache. No second cache key, no second TTL, and nothing that can go stale
    independently of the tenant it belongs to.

    The fallback — a query — is for callers that built a ``Tenant`` themselves
    (the console, management commands, tests) and for the one request after a
    deploy whose cached tenant predates this relation.
    """
    if tenant is None:
        return UNLIMITED

    if not isinstance(tenant, int):
        cached_row = tenant._state.fields_cache.get(RELATION, _UNSET)
        if cached_row is not _UNSET:
            return UNLIMITED if cached_row is None else cached_row.as_policy()
        tenant_id = tenant.pk
    else:
        tenant_id = tenant

    if not tenant_id:
        return UNLIMITED

    from .models import TenantRatePolicy

    row = TenantRatePolicy.objects.filter(tenant_id=tenant_id).first()
    return UNLIMITED if row is None else row.as_policy()


# --------------------------------------------------------------------------- #
# Enforcement
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Refusal:
    """Why a request is being turned away, and for how long."""

    kind: str  # "blocked" | "throttled"
    retry_after: int = 1
    window: str = ""
    limit: int | None = None


def _incr(key: str, ttl: int) -> int:
    """Add-or-increment, atomically enough for a budget.

    Lifted in shape from ``core.ratelimit.hit`` — the same race (a key expiring
    between ``add`` and ``incr``) and the same answer to it.
    """
    try:
        added = cache.add(key, 1, ttl)
        return 1 if added else cache.incr(key)
    except ValueError:  # expired between add() and incr()
        cache.set(key, 1, ttl)
        return 1


def gate(tenant_id: int, policy: Policy, *, now: float | None = None) -> Refusal | None:
    """Spend one unit of budget. ``None`` means the request may proceed.

    A blocked tenant is refused without touching any counter: there is no
    budget to spend and no reason to pay for a key.
    """
    if policy.blocked:
        return Refusal("blocked", retry_after=60)

    now = time.time() if now is None else now

    for name, seconds, field_name in WINDOWS:
        if getattr(policy, field_name) is None:
            continue  # the whole point: an unlimited window is never counted
        ceiling = policy.limit_for(seconds)
        bucket = int(now // seconds)
        count = _incr(f"{PREFIX}{tenant_id}:rl:{seconds}:{bucket}", seconds + 1)
        if count > ceiling:
            # Whole seconds, and never zero: a Retry-After of 0 invites the
            # client straight back into the same wall.
            retry_after = max(1, int((bucket + 1) * seconds - now) or 1)
            return Refusal("throttled", retry_after=retry_after, window=name, limit=ceiling)

    return None


# --------------------------------------------------------------------------- #
# Metering
# --------------------------------------------------------------------------- #


@dataclass
class _Slot:
    """One tenant's traffic within one wall-clock second, in this worker."""

    n: int = 0
    c4: int = 0
    c5: int = 0
    ms: int = 0
    throttled: int = 0


@dataclass
class _Buffer:
    second: int = 0
    slots: dict[int, _Slot] = field(default_factory=dict)


_lock = threading.Lock()
_buffer = _Buffer()


def record(tenant_id: int, status: int, duration_ms: int, *, throttled: bool = False) -> None:
    """Count one request. This is the hot path — keep it a dict update.

    The flush of the *previous* second happens here too, but outside the lock:
    holding a worker-wide lock across nine cache round-trips would serialise
    every request in the process for the duration.
    """
    if not tenant_id:
        return

    pending = None
    with _lock:
        second = int(time.time())
        if second != _buffer.second:
            if _buffer.slots:
                pending = (_buffer.second, _buffer.slots)
            _buffer.second = second
            _buffer.slots = {}

        slot = _buffer.slots.get(tenant_id)
        if slot is None:
            slot = _buffer.slots[tenant_id] = _Slot()
        slot.n += 1
        slot.ms += max(0, duration_ms)
        if throttled:
            slot.throttled += 1
        if 400 <= status < 500:
            slot.c4 += 1
        elif status >= 500:
            slot.c5 += 1

    if pending is not None:
        _write(*pending)


def flush() -> None:
    """Push whatever is buffered to the cache right now.

    For tests, for a shutdown hook, and for the console's own refresh — a
    screen that says "0 requests" because the last second has not rolled over
    yet is a screen that lies.
    """
    with _lock:
        pending = (_buffer.second, _buffer.slots) if _buffer.slots else None
        _buffer.slots = {}
    if pending is not None:
        _write(*pending)


def discard() -> None:
    """Throw the buffer away without writing it.

    For tests, and only for tests. The buffer is worker-local state that
    outlives any one test, while the cache underneath it is wiped between them
    (see the project ``_clear_cache`` fixture) — and tenant primary keys repeat
    across tests. Flushing at the start of a test therefore writes some earlier
    test's requests onto this one's tenant. Dropping is the correct reset;
    :func:`flush` is for production, where nothing is ever discarded.
    """
    with _lock:
        _buffer.slots = {}


def _write(second: int, slots: dict[int, _Slot]) -> None:
    """One second's aggregate for every tenant that was active in it.

    Nine cache operations per active tenant, once per second per worker —
    amortised across every request in that second, which is the whole reason
    for the buffer. Never raises into a request: a monitoring counter is not
    worth a 500, and a cache that is down is exactly when the app must keep
    serving.
    """
    minute = second // 60
    hour = second // 3600
    for tenant_id, slot in slots.items():
        base = f"{PREFIX}{tenant_id}"
        try:
            _incr_by(f"{base}:s:{second}", slot.n, SECOND_TTL)
            _incr_by(f"{base}:m:{minute}:n", slot.n, MINUTE_TTL)
            if slot.c4:
                _incr_by(f"{base}:m:{minute}:c4", slot.c4, MINUTE_TTL)
            if slot.c5:
                _incr_by(f"{base}:m:{minute}:c5", slot.c5, MINUTE_TTL)
            if slot.throttled:
                _incr_by(f"{base}:m:{minute}:tt", slot.throttled, MINUTE_TTL)
            _incr_by(f"{base}:m:{minute}:ms", slot.ms, MINUTE_TTL)
            _incr_by(f"{base}:h:{hour}:n", slot.n, HOUR_TTL)
            if slot.c5:
                _incr_by(f"{base}:h:{hour}:c5", slot.c5, HOUR_TTL)
            cache.set(f"{base}:seen", second, SEEN_TTL)
        except Exception:  # pragma: no cover - cache outage
            logger.warning("Traffic flush failed for tenant %s", tenant_id, exc_info=True)


def _incr_by(key: str, amount: int, ttl: int) -> int:
    try:
        added = cache.add(key, amount, ttl)
        return amount if added else cache.incr(key, amount)
    except ValueError:
        cache.set(key, amount, ttl)
        return amount


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Snapshot:
    """One tenant's traffic, as the console shows it."""

    tenant_id: int
    rps: float = 0.0
    rpm: int = 0
    period_total: int = 0
    period_minutes: int = 1
    errors_4xx: int = 0
    errors_5xx: int = 0
    throttled: int = 0
    ok_responses: int = 0
    avg_ms: int = 0
    #: Mean number of requests in flight, by Little's Law (total service time
    #: divided by elapsed time). A *measured* in-flight gauge would need a
    #: counter incremented and decremented on every request — two cache round
    #: trips each — to say something this arithmetic already says from numbers
    #: we are collecting anyway.
    concurrency: float = 0.0
    #: Total seconds of server time this client consumed in the period. The
    #: honest per-tenant resource figure: CPU cannot be attributed per tenant
    #: inside a shared worker process, but service time can, and it is what
    #: actually competes for the workers.
    service_seconds: float = 0.0
    last_seen: int | None = None
    day_total: int = 0
    day_errors: int = 0

    @property
    def error_rate(self) -> float:
        if not self.period_total:
            return 0.0
        return round((self.errors_4xx + self.errors_5xx) * 100 / self.period_total, 1)


def snapshot(tenant_ids, *, minutes: int = 5, now: int | None = None) -> dict[int, Snapshot]:
    """Traffic for many tenants in a **single** ``get_many``.

    The console's list page calls this once for every client on screen, so it
    is written as one batched read rather than a loop of them: a monitoring
    page that costs a round-trip per row is a monitoring page that becomes the
    load it was built to watch.
    """
    tenant_ids = [int(t) for t in tenant_ids]
    if not tenant_ids:
        return {}

    minutes = max(1, min(MAX_MINUTES, int(minutes)))
    now = int(time.time()) if now is None else int(now)
    minute = now // 60
    hour = now // 3600

    wanted: list[str] = []
    for tenant_id in tenant_ids:
        base = f"{PREFIX}{tenant_id}"
        wanted += [f"{base}:s:{now - offset}" for offset in range(RPS_WINDOW)]
        for offset in range(minutes):
            slot = minute - offset
            wanted += [
                f"{base}:m:{slot}:n",
                f"{base}:m:{slot}:c4",
                f"{base}:m:{slot}:c5",
                f"{base}:m:{slot}:tt",
                f"{base}:m:{slot}:ms",
            ]
        wanted += [f"{base}:h:{hour - offset}:n" for offset in range(24)]
        wanted += [f"{base}:h:{hour - offset}:c5" for offset in range(24)]
        wanted.append(f"{base}:seen")

    try:
        found = cache.get_many(wanted)
    except Exception:  # pragma: no cover - cache outage
        logger.warning("Traffic snapshot read failed", exc_info=True)
        found = {}

    def value(key: str) -> int:
        return int(found.get(key) or 0)

    result = {}
    for tenant_id in tenant_ids:
        base = f"{PREFIX}{tenant_id}"

        # The current second is still being written to by other workers and by
        # this one's own buffer, so it is deliberately excluded from the
        # average: including it makes every reading dip at the moment you look.
        window = [value(f"{base}:s:{now - offset}") for offset in range(1, RPS_WINDOW)]
        rps = round(sum(window) / len(window), 2) if window else 0.0

        rpm = value(f"{base}:m:{minute - 1}:n") or value(f"{base}:m:{minute}:n")

        total = c4 = c5 = throttled = total_ms = 0
        for offset in range(minutes):
            slot = minute - offset
            total += value(f"{base}:m:{slot}:n")
            c4 += value(f"{base}:m:{slot}:c4")
            c5 += value(f"{base}:m:{slot}:c5")
            throttled += value(f"{base}:m:{slot}:tt")
            total_ms += value(f"{base}:m:{slot}:ms")

        day_total = sum(value(f"{base}:h:{hour - offset}:n") for offset in range(24))
        day_errors = sum(value(f"{base}:h:{hour - offset}:c5") for offset in range(24))

        elapsed = minutes * 60
        result[tenant_id] = Snapshot(
            tenant_id=tenant_id,
            rps=rps,
            rpm=rpm,
            period_total=total,
            period_minutes=minutes,
            errors_4xx=c4,
            errors_5xx=c5,
            throttled=throttled,
            ok_responses=max(0, total - c4 - c5),
            avg_ms=int(total_ms / total) if total else 0,
            concurrency=round(total_ms / 1000 / elapsed, 2) if elapsed else 0.0,
            service_seconds=round(total_ms / 1000, 1),
            last_seen=found.get(f"{base}:seen") or None,
            day_total=day_total,
            day_errors=day_errors,
        )
    return result


def series(tenant_id: int, *, minutes: int = 30, now: int | None = None) -> list[dict]:
    """Per-minute counts for one tenant, oldest first — the detail sparkline."""
    minutes = max(1, min(MAX_MINUTES, int(minutes)))
    now = int(time.time()) if now is None else int(now)
    minute = now // 60
    base = f"{PREFIX}{tenant_id}"

    keys = []
    for offset in range(minutes):
        slot = minute - offset
        keys += [f"{base}:m:{slot}:n", f"{base}:m:{slot}:c4", f"{base}:m:{slot}:c5"]
    try:
        found = cache.get_many(keys)
    except Exception:  # pragma: no cover - cache outage
        found = {}

    rows = []
    for offset in reversed(range(minutes)):
        slot = minute - offset
        rows.append(
            {
                "minute": slot * 60,
                "total": int(found.get(f"{base}:m:{slot}:n") or 0),
                "c4": int(found.get(f"{base}:m:{slot}:c4") or 0),
                "c5": int(found.get(f"{base}:m:{slot}:c5") or 0),
            }
        )
    return rows


def reset(tenant_id: int) -> None:
    """Forget a tenant's counters. Used by tests and by nothing else.

    Deliberately not exposed in the console: an operator who can erase the
    evidence of a traffic spike is an operator who can erase the evidence of a
    traffic spike.
    """
    now = int(time.time())
    minute, hour = now // 60, now // 3600
    base = f"{PREFIX}{tenant_id}"
    keys = [f"{base}:seen"]
    keys += [f"{base}:s:{now - offset}" for offset in range(SECOND_TTL)]
    for offset in range(MAX_MINUTES + 1):
        slot = minute - offset
        keys += [f"{base}:m:{slot}:{suffix}" for suffix in ("n", "c4", "c5", "tt", "ms")]
    for offset in range(25):
        keys += [f"{base}:h:{hour - offset}:n", f"{base}:h:{hour - offset}:c5"]
    for _name, seconds, _field in WINDOWS:
        bucket = int(now // seconds)
        keys += [f"{base}:rl:{seconds}:{bucket}", f"{base}:rl:{seconds}:{bucket - 1}"]
    cache.delete_many(keys)


# --------------------------------------------------------------------------- #
# The host the workers run on
# --------------------------------------------------------------------------- #


def server_metrics() -> dict:
    """Load and memory for the *machine*, where the machine will say.

    Not per tenant, and never presented as such: inside one worker process
    serving many centers there is no honest way to attribute CPU or RSS to one
    of them (``Snapshot.service_seconds`` is the figure that *can* be
    attributed). This is context for the operator — "the box is at 90%" — and
    every field is ``None`` where the platform does not offer it, rather than
    dragging in a dependency to guess.
    """
    metrics: dict = {"load1": None, "cpu_percent": None, "memory_percent": None, "cpus": None}

    try:
        metrics["cpus"] = os.cpu_count()
    except Exception:  # pragma: no cover
        pass

    if hasattr(os, "getloadavg"):  # POSIX only; absent on Windows
        try:
            load1 = os.getloadavg()[0]
            metrics["load1"] = round(load1, 2)
            if metrics["cpus"]:
                metrics["cpu_percent"] = min(100, round(load1 * 100 / metrics["cpus"]))
        except OSError:  # pragma: no cover
            pass

    try:  # Linux only, and free — no psutil, no subprocess
        with open("/proc/meminfo", encoding="ascii") as handle:
            info = {}
            for line in handle:
                name, _colon, rest = line.partition(":")
                info[name] = int(rest.split()[0])
        total, available = info.get("MemTotal"), info.get("MemAvailable")
        if total and available is not None:
            metrics["memory_percent"] = round((total - available) * 100 / total)
    except (OSError, ValueError, IndexError, KeyError):
        pass

    return metrics


def cache_is_shared() -> bool:
    """Whether the configured cache can be seen by every worker.

    LocMem is per-process, which quietly turns one limit into one limit *per
    worker* and shows the console a fraction of the real traffic. Worth saying
    out loud in both places it matters.
    """
    from django.conf import settings

    backend = (settings.CACHES.get("default", {}) or {}).get("BACKEND", "")
    return "locmem" not in backend.lower() and "dummy" not in backend.lower()
