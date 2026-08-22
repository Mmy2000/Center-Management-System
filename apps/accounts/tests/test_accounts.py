import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.accounts.permissions import GROUP_NAMES


def test_user_model_is_swapped():
    assert get_user_model() is User
    assert get_user_model()._meta.label == "accounts.User"


def test_role_choices_match_the_documented_matrix():
    assert set(Role.values) == {
        "SUPER_ADMIN",
        "CENTER_ADMIN",
        "INSTRUCTOR",
        "CASHIER",
        "SCAN_OPERATOR",
    }


@pytest.mark.django_db
def test_seed_roles_is_idempotent():
    call_command("seed_roles", verbosity=0)
    first = {g.name: set(g.permissions.values_list("id", flat=True)) for g in Group.objects.all()}
    call_command("seed_roles", verbosity=0)
    second = {g.name: set(g.permissions.values_list("id", flat=True)) for g in Group.objects.all()}
    assert first == second
    assert set(first) == set(GROUP_NAMES.values())


@pytest.mark.django_db
def test_user_is_placed_in_the_group_matching_their_role(user_factory):
    call_command("seed_roles", verbosity=0)
    user = user_factory(username="cash", role=Role.CASHIER)
    assert list(user.groups.values_list("name", flat=True)) == ["CASHIER"]

    user.role = Role.INSTRUCTOR
    user.save()
    assert list(user.groups.values_list("name", flat=True)) == ["INSTRUCTOR"]


def test_cashier_declares_money_permissions_and_no_student_edit():
    """Declaration-level check: the payments app ships in Phase 9, so the
    permission objects themselves do not exist yet."""
    from apps.accounts.permissions import ROLE_PERMISSIONS

    cashier = ROLE_PERMISSIONS[Role.CASHIER]
    assert "payments.add_payment" in cashier
    assert "payments.refund_payment" not in cashier  # refunds need an admin
    assert "students.view_student" in cashier
    assert "students.change_student" not in cashier


@pytest.mark.django_db
def test_role_permissions_are_enforced_at_runtime(user_factory):
    """Uses permissions that exist today (core.Setting)."""
    call_command("seed_roles", verbosity=0)
    admin = User.objects.get(pk=user_factory(username="ca", role=Role.CENTER_ADMIN).pk)
    operator = User.objects.get(pk=user_factory(username="so", role=Role.SCAN_OPERATOR).pk)

    assert admin.has_perm("core.change_setting")
    assert not operator.has_perm("core.change_setting")


@pytest.mark.django_db
def test_super_admin_group_holds_every_permission(user_factory):
    from django.contrib.auth.models import Permission

    call_command("seed_roles", verbosity=0)
    group = Group.objects.get(name="SUPER_ADMIN")
    assert group.permissions.count() == Permission.objects.count()


@pytest.mark.django_db
def test_login_is_throttled(client, user_factory):
    user_factory(username="target", password="RightPass!2026")
    url = reverse("accounts:login")
    for _ in range(5):
        client.post(url, {"username": "target", "password": "wrong"})
    response = client.post(url, {"username": "target", "password": "wrong"})
    assert response.status_code == 429


@pytest.mark.django_db
def test_successful_login_records_ip(client, user_factory):
    user = user_factory(username="ipuser", password="RightPass!2026")
    client.post(
        reverse("accounts:login"),
        {"username": "ipuser", "password": "RightPass!2026"},
        REMOTE_ADDR="192.0.2.10",
    )
    user.refresh_from_db()
    assert user.last_login_ip == "192.0.2.10"


@pytest.mark.django_db
def test_must_change_password_forces_redirect(client, user_factory):
    user_factory(username="fresh", password="RightPass!2026", must_change_password=True)
    client.login(username="fresh", password="RightPass!2026")

    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302
    assert response.url == reverse("accounts:password_change")

    # ...but the change-password page itself must stay reachable.
    assert client.get(reverse("accounts:password_change")).status_code == 200


@pytest.mark.django_db
def test_dashboard_requires_login(client):
    response = client.get(reverse("dashboard:home"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response.url
