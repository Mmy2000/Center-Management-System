"""The sign-in screen: AJAX path, plain-form fallback, and the guards on both."""

import json

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import Role

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def account(user_factory):
    call_command("seed_roles", verbosity=0)
    return user_factory(username="reception", role=Role.CENTER_ADMIN, password=PASSWORD)


def post(client, payload, **extra):
    return client.post(
        reverse("accounts:login_ajax"),
        data=json.dumps(payload),
        content_type="application/json",
        **extra,
    )


# ------------------------------------------------------------------- page --

def test_login_page_renders_with_its_own_stylesheet(client):
    response = client.get(reverse("accounts:login"))
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert "css/auth.css" in body
    assert reverse("accounts:login_ajax") in body
    assert 'id="login-form"' in body
    # Progressive enhancement: the form still posts on its own without JS.
    assert f'action="{reverse("accounts:login")}"' in body


def test_page_carries_the_next_parameter_into_the_form(client):
    response = client.get(reverse("accounts:login") + "?next=/students/")
    assert 'value="/students/"' in response.content.decode("utf-8")


# ------------------------------------------------------------------- ajax --

def test_successful_sign_in_returns_a_redirect(client, account):
    response = post(client, {"username": "reception", "password": PASSWORD})
    body = response.json()

    assert response.status_code == 200
    assert body["ok"] is True
    assert body["data"]["redirect"] == reverse("dashboard:home")
    assert body["data"]["user"]["name"]
    assert client.get(reverse("dashboard:home")).status_code == 200  # session established


def test_wrong_password_is_401_and_does_not_say_which_half_was_wrong(client, account):
    wrong_password = post(client, {"username": "reception", "password": "nope"})
    unknown_user = post(client, {"username": "ghost", "password": "nope"})

    assert wrong_password.status_code == 401
    assert wrong_password.json()["code"] == "ERR_INVALID_CREDENTIALS"
    # Same message either way — no account enumeration.
    assert wrong_password.json()["message"] == unknown_user.json()["message"]


def test_empty_fields_come_back_as_field_errors(client, account):
    response = post(client, {"username": "", "password": ""})
    assert response.status_code == 401
    assert set(response.json()["field_errors"]) == {"username", "password"}


def test_inactive_account_cannot_sign_in(client, account):
    account.is_active = False
    account.save()

    refused = post(client, {"username": "reception", "password": PASSWORD})
    wrong_password = post(client, {"username": "reception", "password": "nope"})

    assert refused.status_code == 401
    # "This account is inactive" would confirm the username exists.
    assert refused.json()["message"] == wrong_password.json()["message"]


def test_the_plain_form_hides_which_half_was_wrong_too(client, account):
    """The no-JavaScript path must not leak more than the AJAX one."""
    unknown = client.post(reverse("accounts:login"), {"username": "ghost", "password": "nope"})
    wrong = client.post(reverse("accounts:login"), {"username": "reception", "password": "nope"})

    assert unknown.context["form"].non_field_errors() == wrong.context["form"].non_field_errors()
    assert "case-sensitive" not in wrong.content.decode("utf-8")


def test_a_user_who_must_change_password_is_sent_there(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="fresh", password=PASSWORD, must_change_password=True)

    response = post(client, {"username": "fresh", "password": PASSWORD})
    assert response.json()["data"]["redirect"] == reverse("accounts:password_change")


def test_next_is_honoured_when_it_is_local(client, account):
    response = post(
        client, {"username": "reception", "password": PASSWORD, "next": "/students/"}
    )
    assert response.json()["data"]["redirect"] == "/students/"


@pytest.mark.parametrize(
    "hostile",
    ["https://evil.example.com/steal", "//evil.example.com", "http://evil.example.com"],
)
def test_next_cannot_bounce_to_another_host(client, account, hostile):
    """Open-redirect guard: a crafted ?next= must not leave the site."""
    response = post(client, {"username": "reception", "password": PASSWORD, "next": hostile})
    assert response.json()["data"]["redirect"] == reverse("dashboard:home")


def test_ajax_and_form_share_one_throttle(client, account):
    """Five failures on either path, and the sixth is refused on both."""
    for _ in range(3):
        post(client, {"username": "reception", "password": "wrong"})
    for _ in range(2):
        client.post(reverse("accounts:login"), {"username": "reception", "password": "wrong"})

    throttled = post(client, {"username": "reception", "password": PASSWORD})
    assert throttled.status_code == 429
    assert throttled.json()["code"] == "ERR_RATE_LIMITED"


def test_plain_form_post_still_signs_in(client, account):
    """No JavaScript: the ordinary Django login view keeps working."""
    response = client.post(
        reverse("accounts:login"), {"username": "reception", "password": PASSWORD}
    )
    assert response.status_code == 302
    assert client.get(reverse("dashboard:home")).status_code == 200


def test_login_endpoint_rejects_get(client):
    assert client.get(reverse("accounts:login_ajax")).status_code == 405
