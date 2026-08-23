import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _clear_cache():
    """Policies and rate limits live in the cache — never leak between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user_factory(db):
    from apps.accounts.models import Role, User

    def make(username="staff", role=Role.CENTER_ADMIN, password="TestPass!2026", **extra):
        return User.objects.create_user(username=username, password=password, role=role, **extra)

    return make


@pytest.fixture
def admin_user_(user_factory):
    from apps.accounts.models import Role

    return user_factory(username="admin1", role=Role.SUPER_ADMIN, is_superuser=True, is_staff=True)


@pytest.fixture(autouse=True)
def default_tenant(request):
    """Every database test runs inside one center, served at ``testserver``.

    The suite predates tenancy: 562 tests call ``Student.objects...`` and
    ``client.get("/students/")`` with no idea a tenant exists. Rather than
    editing all of them, this fixture supplies the thing they now assume — a
    tenant in context, and a Domain the test client's Host header resolves to.

    That is also the honest shape of production: every request runs inside
    exactly one tenant, and code that forgets it raises rather than guessing.

    Tests that need *two* centers (the isolation suite) create their own and
    switch contexts explicitly; ``apps/tenancy/conftest.py`` opts that package
    out of this fixture entirely.
    """
    # `_django_db_helper` is what the `django_db` *marker* pulls in; `db` and
    # `transactional_db` are what a test asks for by name. A test that uses none
    # of them touches no database and needs no tenant.
    wanted = [
        name
        for name in ("_django_db_helper", "transactional_db", "db")
        if name in request.fixturenames
    ]
    if not wanted:
        yield None
        return
    # Being *named* in fixturenames is not the same as being set up: request it
    # explicitly, or the first ORM call below runs before the test database is.
    request.getfixturevalue(wanted[0])

    from apps.tenancy.constants import TenantStatus
    from apps.tenancy.context import tenant_context
    from apps.tenancy.models import Domain, Plan, Tenant

    plan, _ = Plan.objects.get_or_create(
        slug="test", defaults={"name": "Test plan", "is_public": False}
    )
    tenant = Tenant.objects.create(
        slug="testcenter", name="سنتر الاختبار", status=TenantStatus.ACTIVE, plan=plan
    )
    Domain.objects.create(tenant=tenant, host="testserver", is_primary=True)

    with tenant_context(tenant):
        yield tenant


@pytest.fixture(scope="session", autouse=True)
def _tenancy_test_tables(django_db_setup, django_db_blocker):
    """Real tables for ``apps/tenancy/tests/models.py``, for the whole session.

    Those two throwaway models prove ``TenantOwnedModel`` itself. They are
    registered the moment pytest *collects* the tenancy tests — before any test
    runs — so their tables have to exist for the whole session too. Anything
    narrower leaves a registered tenant-owned model with no table behind it, and
    every sweep over ``apps.get_models()`` (the export, the purge command, the
    leak suite) then fails in whichever unrelated test happens to run next.

    Lives here rather than in ``apps/tenancy/conftest.py`` for exactly that
    reason: running only ``pytest apps/console`` must still find the tables.
    """
    from django.db import connection

    from apps.tenancy.tests.models import Gadget, Widget

    with django_db_blocker.unblock(), connection.schema_editor() as editor:
        editor.create_model(Widget)
        editor.create_model(Gadget)
    yield
    with django_db_blocker.unblock(), connection.schema_editor() as editor:
        editor.delete_model(Gadget)
        editor.delete_model(Widget)
