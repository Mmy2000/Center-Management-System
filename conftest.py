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
