"""TASK-091 — the contextvar must never leak.

A leaked tenant means the next request served by that worker reads another
center's data. Everything here is about the ``finally``.
"""

import threading

import pytest

from apps.tenancy.context import (
    all_tenants_context,
    current_tenant,
    current_tenant_id,
    is_all_tenants,
    require_tenant,
    reset_tenant,
    set_tenant,
    tenant_context,
)
from apps.tenancy.exceptions import TenantContextRequired

from .factories import make_tenant, make_two_tenants


def test_default_is_none():
    assert current_tenant() is None
    assert current_tenant_id() is None


def test_require_tenant_raises_when_absent():
    with pytest.raises(TenantContextRequired):
        require_tenant()


@pytest.mark.django_db
def test_context_sets_and_restores():
    tenant = make_tenant()
    assert current_tenant() is None
    with tenant_context(tenant):
        assert current_tenant() == tenant
        assert current_tenant_id() == tenant.pk
        assert require_tenant() == tenant
    assert current_tenant() is None


@pytest.mark.django_db
def test_nested_context_restores_outer():
    alpha, beta = make_two_tenants()
    with tenant_context(alpha):
        with tenant_context(beta):
            assert current_tenant() == beta
        # The token restores the *previous* value, not "whatever was last set".
        assert current_tenant() == alpha
    assert current_tenant() is None


@pytest.mark.django_db
def test_exception_inside_block_still_restores():
    tenant = make_tenant()
    with pytest.raises(ValueError):  # noqa: PT012 - the raise is the point
        with tenant_context(tenant):
            raise ValueError("boom")
    assert current_tenant() is None


@pytest.mark.django_db
def test_low_level_set_and_reset():
    tenant = make_tenant()
    token = set_tenant(tenant)
    try:
        assert current_tenant() == tenant
    finally:
        reset_tenant(token)
    assert current_tenant() is None


@pytest.mark.django_db(transaction=True)
def test_threads_do_not_share_a_tenant():
    """Two threads, two tenants, interleaved reads — no cross-talk.

    This is the WSGI worker scenario: one process, several threads, each serving
    a different center at the same moment.
    """
    alpha, beta = make_two_tenants()
    errors: list[str] = []
    barrier = threading.Barrier(2)

    def worker(tenant, other):
        with tenant_context(tenant):
            for _ in range(100):
                barrier.wait()
                if current_tenant_id() != tenant.pk:
                    errors.append(f"{tenant.slug} saw {current_tenant_id()} (expected {tenant.pk})")
                    return
                if current_tenant_id() == other.pk:
                    errors.append(f"{tenant.slug} leaked into {other.slug}")
                    return

    threads = [
        threading.Thread(target=worker, args=(alpha, beta)),
        threading.Thread(target=worker, args=(beta, alpha)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []


@pytest.mark.django_db
def test_all_tenants_marker_is_scoped():
    assert is_all_tenants() is False
    with all_tenants_context():
        assert is_all_tenants() is True
    assert is_all_tenants() is False


@pytest.mark.django_db
def test_all_tenants_marker_does_not_disable_managers():
    """The marker records intent; it never silently unfilters a query.

    Crossing tenants is always an explicit ``Model.all_tenants``, so that the
    escape hatch stays greppable.
    """
    from apps.tenancy.models import Tenant

    make_tenant()
    with all_tenants_context():
        # Tenant itself is control-plane, not tenant-owned — readable as normal.
        assert Tenant.objects.count() == 1
        assert current_tenant() is None
