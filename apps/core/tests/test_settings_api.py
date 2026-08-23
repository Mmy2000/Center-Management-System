import json

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import Role
from apps.core.models import AuditAction, AuditLog
from apps.core.registry import settings_registry

PASSWORD = "TestPass!2026"


@pytest.fixture
def admin_client_(client, user_factory, db):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def _post(client, payload):
    return client.post(
        reverse("core_api:settings_update"),
        data=json.dumps(payload),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_settings_list_returns_every_policy(admin_client_):
    response = admin_client_.get(reverse("core_api:settings_list"))
    assert response.status_code == 200
    groups = response.json()["data"]["groups"]
    keys = {item["key"] for group in groups for item in group["items"]}
    from apps.core.policies import SPECS

    assert keys == {s.key for s in SPECS}


@pytest.mark.django_db
def test_update_applies_immediately_and_audits(admin_client_):
    response = _post(
        admin_client_,
        {"values": {"attendance.late_after_minutes": 20}, "reason": "قرار الإدارة"},
    )
    assert response.status_code == 200
    assert settings_registry.get("attendance.late_after_minutes") == 20

    entry = AuditLog.objects.get(action=AuditAction.SETTING_CHANGED)
    assert entry.changes["attendance.late_after_minutes"] == {"old": 15, "new": 20}
    assert entry.reason == "قرار الإدارة"


@pytest.mark.django_db
def test_payment_enforcement_toggle_takes_effect_without_restart(admin_client_):
    assert settings_registry.get("payments.enforce_on_attendance") is False
    _post(admin_client_, {"values": {"payments.enforce_on_attendance": True}})
    assert settings_registry.get("payments.enforce_on_attendance") is True


@pytest.mark.django_db
def test_invalid_value_is_rejected_per_field(admin_client_):
    response = _post(admin_client_, {"values": {"attendance.late_after_minutes": -3}})
    assert response.status_code == 400
    assert "attendance.late_after_minutes" in response.json()["field_errors"]
    assert settings_registry.get("attendance.late_after_minutes") == 15


@pytest.mark.django_db
def test_unknown_key_is_rejected(admin_client_):
    response = _post(admin_client_, {"values": {"attendance.nope": 1}})
    assert response.status_code == 400
    assert "attendance.nope" in response.json()["field_errors"]


@pytest.mark.django_db
def test_settings_write_requires_permission(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="op", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op", password=PASSWORD)
    assert _post(client, {"values": {"payments.grace_days": 2}}).status_code == 403
    assert client.get(reverse("core:settings")).status_code == 403


# ------------------------------------------------------------- appearance #


@pytest.mark.django_db
def test_appearance_defaults_are_exposed_to_templates(admin_client_):
    from django.urls import reverse as url

    response = admin_client_.get(url("dashboard:home"))
    html = response.content.decode("utf-8")
    assert 'data-theme="teal"' in html
    assert 'data-default-accent="#1f6f8b"' in html
    assert 'data-density="comfortable"' in html


@pytest.mark.django_db
def test_theme_choice_is_validated(admin_client_):
    assert _post(admin_client_, {"values": {"ui.theme": "indigo"}}).status_code == 200
    assert settings_registry.get("ui.theme") == "indigo"

    bad = _post(admin_client_, {"values": {"ui.theme": "chartreuse"}})
    assert bad.status_code == 400
    assert "ui.theme" in bad.json()["field_errors"]
    assert settings_registry.get("ui.theme") == "indigo"


@pytest.mark.django_db
def test_accent_must_be_a_hex_colour(admin_client_):
    assert _post(admin_client_, {"values": {"ui.accent": "#b5179e"}}).status_code == 200
    assert settings_registry.get("ui.accent") == "#b5179e"

    for invalid in ("red", "#ff", "1f6f8b;", "#12345g"):
        response = _post(admin_client_, {"values": {"ui.accent": invalid}})
        assert response.status_code == 400, invalid
        assert "ui.accent" in response.json()["field_errors"]
    assert settings_registry.get("ui.accent") == "#b5179e"


@pytest.mark.django_db
def test_changing_the_palette_changes_the_page_and_the_pdf(admin_client_):
    from django.urls import reverse as url

    from apps.core.pdf import brand_colors

    before = brand_colors()[0].hexval()
    _post(admin_client_, {"values": {"ui.theme": "sunset", "ui.mode": "dark"}})

    html = admin_client_.get(url("dashboard:home")).content.decode("utf-8")
    assert 'data-theme="sunset"' in html
    assert 'data-bs-theme="dark"' in html
    assert brand_colors()[0].hexval() != before
