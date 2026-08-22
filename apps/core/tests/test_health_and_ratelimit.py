import pytest

from apps.core import ratelimit
from apps.core.http import DomainError


def test_healthz_is_public(client):
    response = client.get("/healthz/")
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.django_db
def test_readyz_checks_dependencies(client):
    response = client.get("/readyz/")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["cache"] == "ok"


def test_rate_limit_allows_up_to_limit():
    for _ in range(5):
        assert ratelimit.hit("test", "1.2.3.4", limit=5, window=60) is True
    assert ratelimit.hit("test", "1.2.3.4", limit=5, window=60) is False


def test_rate_limit_is_per_identity():
    for _ in range(5):
        ratelimit.hit("test", "a", limit=5, window=60)
    assert ratelimit.hit("test", "b", limit=5, window=60) is True


def test_check_raises_domain_error():
    for _ in range(3):
        ratelimit.hit("scope", "x", limit=3, window=60)
    with pytest.raises(DomainError) as exc:
        ratelimit.check("scope", "x", limit=3, window=60)
    assert exc.value.code == "ERR_RATE_LIMITED"
    assert exc.value.status == 429
