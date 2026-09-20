"""Metering, rate control and the isolation between them (TASK-122).

Split the way the module is: the metering half must never be able to refuse a
request, and the enforcement half must never depend on a buffered number. Most
of what can go wrong here is one of those two halves quietly borrowing from the
other, so the tests keep them apart on purpose.

``traffic.flush()`` appears after every simulated request. In production the
buffer drains on the next second's first request; in a test that runs in three
milliseconds it would never drain at all, and asserting on an empty cache would
prove nothing.
"""

import threading
import time

import pytest
from django.core.cache import cache
from django.test import Client

from apps.tenancy import traffic
from apps.tenancy.constants import TenantStatus, TrafficMode
from apps.tenancy.middleware import TenantTrafficMiddleware
from apps.tenancy.models import TenantRatePolicy
from apps.tenancy.tests.factories import make_tenant, make_two_tenants

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _drain_buffer():
    """A worker's buffer outlives a test; a test's expectations must not.

    Dropped rather than flushed: the project's ``_clear_cache`` fixture wipes
    the cache before this runs, so flushing would write an earlier test's
    requests into this one's — onto the same tenant id, since primary keys
    restart every test.
    """
    traffic.discard()
    yield
    traffic.discard()


def client_for(tenant) -> Client:
    client = Client()
    client.defaults["HTTP_HOST"] = tenant.primary_host
    return client


# --------------------------------------------------------------- metering --


def test_a_request_is_counted_against_its_tenant():
    tenant = make_tenant("alpha")

    client_for(tenant).get("/")
    traffic.flush()

    snapshot = traffic.snapshot([tenant.pk])[tenant.pk]
    assert snapshot.period_total == 1
    assert snapshot.last_seen is not None


def test_counts_are_per_tenant_and_never_pooled():
    """The whole point of the key namespace, asserted rather than assumed."""
    alpha, beta = make_two_tenants()

    for _ in range(3):
        client_for(alpha).get("/")
    client_for(beta).get("/")
    traffic.flush()

    snapshots = traffic.snapshot([alpha.pk, beta.pk])
    assert snapshots[alpha.pk].period_total == 3
    assert snapshots[beta.pk].period_total == 1


def test_status_classes_are_split_and_the_error_rate_follows():
    tenant = make_tenant("alpha")

    traffic.record(tenant.pk, 200, 10)
    traffic.record(tenant.pk, 200, 10)
    traffic.record(tenant.pk, 404, 5)
    traffic.record(tenant.pk, 500, 90)
    traffic.flush()

    snapshot = traffic.snapshot([tenant.pk])[tenant.pk]
    assert (snapshot.ok_responses, snapshot.errors_4xx, snapshot.errors_5xx) == (2, 1, 1)
    assert snapshot.error_rate == 50.0
    assert snapshot.avg_ms == 28  # (10 + 10 + 5 + 90) / 4


def test_service_time_and_concurrency_come_from_the_same_measurement():
    """Little's Law, not a second counter — see ``Snapshot.concurrency``."""
    tenant = make_tenant("alpha")

    for _ in range(60):
        traffic.record(tenant.pk, 200, 1000)  # one second of work, sixty times
    traffic.flush()

    snapshot = traffic.snapshot([tenant.pk], minutes=1)[tenant.pk]
    assert snapshot.service_seconds == 60.0
    # Sixty seconds of work inside a sixty-second window: one request in flight.
    assert snapshot.concurrency == 1.0


def test_a_tenant_with_no_traffic_reads_as_empty_rather_than_missing():
    """A quiet client must render as a row of zeroes, not vanish from the page."""
    tenant = make_tenant("alpha")

    snapshot = traffic.snapshot([tenant.pk])[tenant.pk]
    assert snapshot.period_total == 0
    assert snapshot.rps == 0
    assert snapshot.last_seen is None
    assert snapshot.error_rate == 0.0


def test_snapshot_of_nothing_is_not_an_error():
    assert traffic.snapshot([]) == {}


def test_the_console_host_is_never_metered(settings):
    """An operator watching the numbers must not appear in them."""
    settings.CONSOLE_HOST = "console.testserver"
    tenant = make_tenant("alpha")

    console = Client()
    console.defaults["HTTP_HOST"] = "console.testserver"
    console.get("/login/")
    traffic.flush()

    assert traffic.snapshot([tenant.pk])[tenant.pk].period_total == 0


def test_static_and_health_paths_are_not_metered():
    tenant = make_tenant("alpha")
    client = client_for(tenant)

    client.get("/healthz/")
    client.get("/static/css/app.css")
    traffic.flush()

    assert traffic.snapshot([tenant.pk])[tenant.pk].period_total == 0


def test_the_per_minute_series_is_oldest_first_and_complete():
    """The shape the charts draw: every slot present, oldest first."""
    tenant = make_tenant("alpha")
    traffic.record(tenant.pk, 200, 5)
    traffic.flush()

    rows = traffic.snapshot([tenant.pk], history=5)[tenant.pk].series
    assert len(rows) == 5
    assert [row["minute"] for row in rows] == sorted(row["minute"] for row in rows)
    assert rows[-1]["total"] == 1  # the newest slot is the one just written
    # Quiet minutes are zeroes, not gaps: a chart that skips them would draw a
    # lull as a straight line between the peaks on either side of it.
    assert [row["total"] for row in rows[:-1]] == [0, 0, 0, 0]


def test_history_is_independent_of_the_period():
    """A one-minute period must still leave a sparkline with a shape."""
    tenant = make_tenant("alpha")
    traffic.record(tenant.pk, 200, 5)
    traffic.flush()

    snap = traffic.snapshot([tenant.pk], minutes=1, history=30)[tenant.pk]

    assert snap.period_minutes == 1
    assert len(snap.series) == 30


def test_the_series_splits_status_classes():
    tenant = make_tenant("alpha")
    traffic.record(tenant.pk, 200, 5)
    traffic.record(tenant.pk, 404, 5)
    traffic.record(tenant.pk, 503, 5)
    traffic.flush()

    newest = traffic.snapshot([tenant.pk], history=3)[tenant.pk].series[-1]

    assert (newest["total"], newest["c4"], newest["c5"]) == (3, 1, 1)


# ----------------------------------------------------------------- policy --


def test_absence_of_a_row_means_unlimited():
    tenant = make_tenant("alpha")
    policy = traffic.policy_for(tenant.pk)

    assert policy == traffic.UNLIMITED
    assert policy.mode == TrafficMode.ACTIVE
    assert traffic.gate(tenant.pk, policy) is None


def test_mode_is_derived_from_the_limits_not_stored_beside_them():
    tenant = make_tenant("alpha")
    policy = TenantRatePolicy.objects.create(tenant=tenant, max_rpm=100)

    assert policy.mode == TrafficMode.LIMITED
    policy.blocked = True
    assert policy.mode == TrafficMode.BLOCKED


def test_saving_a_policy_invalidates_the_cached_copy():
    """A block that takes five minutes to bite is not a block."""
    tenant = make_tenant("alpha")
    assert traffic.policy_for(tenant.pk).blocked is False  # warms the cache

    TenantRatePolicy.objects.create(tenant=tenant, blocked=True, blocked_reason="flood")

    assert traffic.policy_for(tenant.pk).blocked is True


def test_deleting_a_policy_invalidates_the_cached_copy():
    tenant = make_tenant("alpha")
    policy = TenantRatePolicy.objects.create(tenant=tenant, max_rps=1)
    assert traffic.policy_for(tenant.pk).max_rps == 1

    policy.delete()

    assert traffic.policy_for(tenant.pk).max_rps is None


def test_burst_applies_to_the_second_window_only():
    policy = traffic.Policy(max_rps=10, max_rpm=100, burst=5)

    assert policy.limit_for(1) == 15
    assert policy.limit_for(60) == 100
    assert policy.limit_for(3600) is None


# ------------------------------------------------------------ enforcement --


def test_an_unlimited_window_is_never_counted():
    """The reason an unconfigured tenant costs nothing on the hot path."""
    tenant = make_tenant("alpha")
    policy = traffic.Policy(max_rpm=60)  # only the minute window has a ceiling

    traffic.gate(tenant.pk, policy)

    second_bucket = int(time.time())
    assert cache.get(f"tf:{tenant.pk}:rl:1:{second_bucket}") is None
    assert cache.get(f"tf:{tenant.pk}:rl:60:{second_bucket // 60}") == 1


def test_the_limit_is_the_limit():
    tenant = make_tenant("alpha")
    policy = traffic.Policy(max_rps=3)
    now = 1_700_000_000.0  # pinned: a real clock would roll the bucket mid-test

    assert [traffic.gate(tenant.pk, policy, now=now) for _ in range(3)] == [None, None, None]

    refusal = traffic.gate(tenant.pk, policy, now=now)
    assert refusal is not None
    assert refusal.kind == "throttled"
    assert refusal.window == "rps"
    assert refusal.limit == 3


def test_budget_refills_when_the_window_rolls_over():
    tenant = make_tenant("alpha")
    policy = traffic.Policy(max_rps=1)

    assert traffic.gate(tenant.pk, policy, now=1_700_000_000.0) is None
    assert traffic.gate(tenant.pk, policy, now=1_700_000_000.5) is not None
    assert traffic.gate(tenant.pk, policy, now=1_700_000_001.0) is None


def test_a_refused_request_does_not_spend_a_longer_window():
    """Else a second-long burst would eat an hour's budget in a minute."""
    tenant = make_tenant("alpha")
    policy = traffic.Policy(max_rps=1, max_rph=1000)
    now = 1_700_000_000.0

    traffic.gate(tenant.pk, policy, now=now)
    traffic.gate(tenant.pk, policy, now=now)  # refused by the second window

    assert cache.get(f"tf:{tenant.pk}:rl:3600:{int(now // 3600)}") == 1


def test_a_blocked_tenant_is_refused_without_spending_anything():
    tenant = make_tenant("alpha")
    policy = traffic.Policy(blocked=True, max_rps=10)

    refusal = traffic.gate(tenant.pk, policy, now=1_700_000_000.0)

    assert refusal.kind == "blocked"
    assert cache.get(f"tf:{tenant.pk}:rl:1:1700000000") is None


def test_retry_after_is_never_zero():
    """A Retry-After of 0 invites the client straight back into the wall."""
    tenant = make_tenant("alpha")
    policy = traffic.Policy(max_rps=1)

    traffic.gate(tenant.pk, policy, now=1_700_000_000.99)
    refusal = traffic.gate(tenant.pk, policy, now=1_700_000_000.99)

    assert refusal.retry_after >= 1


# ------------------------------------------------- enforcement, end to end --


def test_a_throttled_request_gets_429_and_a_page_rather_than_the_view():
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, max_rpm=2)
    client = client_for(tenant)

    assert client.get("/").status_code != 429
    assert client.get("/").status_code != 429

    response = client.get("/")
    assert response.status_code == 429
    assert response["Retry-After"]
    assert response["Cache-Control"] == "no-store"
    assert "ازدحام" in response.content.decode()


def test_a_blocked_tenant_is_refused_on_every_path():
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, blocked=True, blocked_reason="سكربت متعطل")
    client = client_for(tenant)

    response = client.get("/")
    assert response.status_code == 429
    body = response.content.decode()
    assert "سكربت متعطل" in body
    assert "أوقف مشغّل النظام استقبال الطلبات" in body
    # And it must *not* say what the subscription gate says. Telling a paying
    # center their account was suspended because a script of theirs misbehaved
    # is a support call that did not need to happen.
    assert "تم إيقاف الدخول إلى حساب" not in body


def test_health_checks_answer_while_a_tenant_is_blocked():
    """A rate limit that takes the health check down looks like an outage."""
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, blocked=True, blocked_reason="flood")

    assert client_for(tenant).get("/healthz/").status_code == 200


def test_an_ajax_request_gets_the_json_envelope_not_html():
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, blocked=True, blocked_reason="flood")

    response = client_for(tenant).get("/", HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    assert response.status_code == 429
    payload = response.json()
    assert payload["ok"] is False
    assert payload["code"] == "ERR_TENANT_BLOCKED"
    assert payload["data"]["retry_after"] >= 1


def test_a_throttled_request_is_itself_metered():
    """A client being refused is exactly the one the console must see."""
    tenant = make_tenant("alpha")
    TenantRatePolicy.objects.create(tenant=tenant, max_rpm=1)
    client = client_for(tenant)

    client.get("/")
    client.get("/")  # refused
    traffic.flush()

    snapshot = traffic.snapshot([tenant.pk])[tenant.pk]
    assert snapshot.throttled == 1
    assert snapshot.period_total == 2


def test_unblocking_restores_service_immediately():
    tenant = make_tenant("alpha")
    policy = TenantRatePolicy.objects.create(tenant=tenant, blocked=True, blocked_reason="flood")
    client = client_for(tenant)
    assert client.get("/").status_code == 429

    policy.blocked = False
    policy.save()

    assert client.get("/").status_code != 429


# --------------------------------------------------------------- isolation --


def test_blocking_one_center_leaves_the_other_serving():
    """The property everything else here exists to protect."""
    alpha, beta = make_two_tenants()
    TenantRatePolicy.objects.create(tenant=alpha, blocked=True, blocked_reason="flood")

    assert client_for(alpha).get("/").status_code == 429
    assert client_for(beta).get("/").status_code != 429


def test_one_centers_flood_does_not_spend_anothers_budget():
    alpha, beta = make_two_tenants()
    for tenant in (alpha, beta):
        TenantRatePolicy.objects.create(tenant=tenant, max_rpm=3)

    for _ in range(6):
        client_for(alpha).get("/")

    assert client_for(alpha).get("/").status_code == 429
    assert client_for(beta).get("/").status_code != 429


def test_a_suspended_tenant_still_meters_and_gets_the_subscription_gate():
    """The two gates are independent and must not be read as one.

    A suspended center sees the subscription gate (403), not a throttle
    (429), and an operator reading the console has to be able to tell which
    of the two is happening. The traffic is still counted, because a
    suspended center hammering the door is worth seeing.
    """
    tenant = make_tenant("alpha", status=TenantStatus.SUSPENDED)

    response = client_for(tenant).get("/")
    traffic.flush()

    assert response.status_code == 403
    assert "تم إيقاف الدخول إلى حساب" in response.content.decode()
    assert traffic.snapshot([tenant.pk])[tenant.pk].period_total == 1


# --------------------------------------------------------------- concurrency


def test_concurrent_requests_do_not_lose_counts():
    """The buffer is worker-wide; gunicorn runs threads inside a worker.

    Threads here rather than processes because that is the shape that actually
    exists in this deployment — ``--workers 4 --threads 2`` (docs/07 §L.2) —
    and it is the shape the lock is there for.
    """
    tenant = make_tenant("alpha")
    errors = []

    def hammer():
        try:
            for _ in range(50):
                traffic.record(tenant.pk, 200, 1)
        except Exception as exc:  # pragma: no cover - a failure is the assertion
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    traffic.flush()

    assert errors == []
    # Spanning a second boundary splits the total across two minute slots at
    # worst, never across two *minutes*, so a one-minute window holds them all
    # unless the run straddles a minute — hence the generous window.
    snapshot = traffic.snapshot([tenant.pk], minutes=5)
    assert snapshot[tenant.pk].period_total == 200


def test_concurrent_gate_calls_share_one_budget():
    """Two threads must not each get the full allowance."""
    tenant = make_tenant("alpha")
    policy = traffic.Policy(max_rps=10)
    now = 1_700_000_000.0
    allowed = []
    lock = threading.Lock()

    def hammer():
        for _ in range(25):
            if traffic.gate(tenant.pk, policy, now=now) is None:
                with lock:
                    allowed.append(1)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(allowed) == 10


# ------------------------------------------------------------- environment --


def test_the_shared_cache_check_fires_on_locmem(settings):
    from apps.tenancy.checks import check_traffic_cache_is_shared

    assert traffic.cache_is_shared() is False  # the test settings use LocMem
    assert [issue.id for issue in check_traffic_cache_is_shared()] == ["tenancy.W002"]

    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": "x"}
    }
    assert traffic.cache_is_shared() is True
    assert check_traffic_cache_is_shared() == []


def test_server_metrics_never_raise_and_never_invent():
    metrics = traffic.server_metrics()

    assert set(metrics) == {"load1", "cpu_percent", "memory_percent", "cpus"}
    # Every field is either a real reading or an explicit None — never a zero
    # standing in for "this platform cannot tell you".
    for value in metrics.values():
        assert value is None or isinstance(value, (int, float))


def test_metering_survives_a_broken_cache(monkeypatch):
    """A cache outage must not turn into a 500 on a tenant's own site."""
    tenant = make_tenant("alpha")

    def explode(*args, **kwargs):
        raise RuntimeError("redis is gone")

    monkeypatch.setattr(cache, "add", explode)
    monkeypatch.setattr(cache, "set", explode)

    traffic.record(tenant.pk, 200, 5)
    traffic.flush()  # must not raise


def test_a_middleware_refusal_is_a_pure_function_of_the_refusal(rf):
    """``refuse`` is reachable from tests without a full request cycle."""
    tenant = make_tenant("alpha")
    request = rf.get("/")

    response = TenantTrafficMiddleware.refuse(
        request, tenant, traffic.Refusal("throttled", retry_after=7, window="rps", limit=3)
    )

    assert response.status_code == 429
    assert response["Retry-After"] == "7"
