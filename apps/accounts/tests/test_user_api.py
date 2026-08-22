import json

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.core.models import AuditAction, AuditLog

PASSWORD = "TestPass!2026"


@pytest.fixture
def seeded_roles(db):
    call_command("seed_roles", verbosity=0)


@pytest.fixture
def admin_client_(client, user_factory, seeded_roles):
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _patch(client, url, payload):
    return client.patch(url, data=json.dumps(payload), content_type="application/json")


@pytest.mark.django_db
def test_list_users_requires_permission(client, user_factory, seeded_roles):
    user_factory(username="op", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op", password=PASSWORD)
    response = client.get(reverse("accounts_api:users"))
    assert response.status_code == 403
    assert response.json()["code"] == "ERR_FORBIDDEN"


@pytest.mark.django_db
def test_create_user_is_audited(admin_client_):
    response = _post(
        admin_client_,
        reverse("accounts_api:users"),
        {
            "username": "newcashier",
            "password": PASSWORD,
            "role": Role.CASHIER,
            "full_name": "أمين الخزنة",
        },
    )
    assert response.status_code == 200
    body = response.json()["data"]["user"]
    assert body["role"] == Role.CASHIER
    assert body["must_change_password"] is True

    created = User.objects.get(username="newcashier")
    assert list(created.groups.values_list("name", flat=True)) == ["CASHIER"]
    assert AuditLog.objects.filter(action=AuditAction.USER_CREATED, object_id=created.pk).exists()


@pytest.mark.django_db
def test_weak_password_is_rejected(admin_client_):
    response = _post(
        admin_client_,
        reverse("accounts_api:users"),
        {"username": "weak", "password": "123", "role": Role.CASHIER},
    )
    assert response.status_code == 400
    assert "password" in response.json()["field_errors"]
    assert not User.objects.filter(username="weak").exists()


@pytest.mark.django_db
def test_duplicate_username_returns_field_error(admin_client_, user_factory):
    user_factory(username="taken")
    response = _post(
        admin_client_,
        reverse("accounts_api:users"),
        {"username": "taken", "password": PASSWORD, "role": Role.CASHIER},
    )
    assert response.status_code == 400
    assert "username" in response.json()["field_errors"]


@pytest.mark.django_db
def test_center_admin_cannot_grant_super_admin(client, user_factory, seeded_roles):
    user_factory(username="ca", role=Role.CENTER_ADMIN, password=PASSWORD)
    client.login(username="ca", password=PASSWORD)
    response = _post(
        client,
        reverse("accounts_api:users"),
        {"username": "escalated", "password": PASSWORD, "role": Role.SUPER_ADMIN},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ERR_FORBIDDEN_ROLE"
    assert not User.objects.filter(username="escalated").exists()


@pytest.mark.django_db
def test_cannot_deactivate_self(admin_client_):
    boss = User.objects.get(username="boss")
    response = _patch(
        admin_client_,
        reverse("accounts_api:user_detail", args=[boss.pk]),
        {"is_active": False},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ERR_SELF_DEACTIVATION"
    boss.refresh_from_db()
    assert boss.is_active is True


@pytest.mark.django_db
def test_cannot_deactivate_the_last_super_admin(admin_client_, user_factory):
    other = user_factory(username="boss2", role=Role.SUPER_ADMIN)
    User.objects.filter(username="boss").update(role=Role.CENTER_ADMIN)

    response = _patch(
        admin_client_,
        reverse("accounts_api:user_detail", args=[other.pk]),
        {"is_active": False},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ERR_LAST_SUPER_ADMIN"


@pytest.mark.django_db
def test_role_change_is_audited_and_syncs_group(admin_client_, user_factory):
    target = user_factory(username="mover", role=Role.SCAN_OPERATOR)
    response = _patch(
        admin_client_,
        reverse("accounts_api:user_detail", args=[target.pk]),
        {"role": Role.INSTRUCTOR, "reason": "أصبح مدرّسًا"},
    )
    assert response.status_code == 200

    target.refresh_from_db()
    assert target.role == Role.INSTRUCTOR
    assert list(target.groups.values_list("name", flat=True)) == ["INSTRUCTOR"]

    entry = AuditLog.objects.filter(action=AuditAction.USER_UPDATED, object_id=target.pk).first()
    assert entry.changes["role"] == {"old": "SCAN_OPERATOR", "new": "INSTRUCTOR"}
    assert entry.reason == "أصبح مدرّسًا"


@pytest.mark.django_db
def test_users_page_requires_permission(client, user_factory, seeded_roles):
    user_factory(username="op2", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op2", password=PASSWORD)
    assert client.get(reverse("accounts:users")).status_code == 403
