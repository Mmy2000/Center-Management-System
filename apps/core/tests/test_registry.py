import pytest
from django.core.exceptions import ValidationError

from apps.core.models import Setting
from apps.core.registry import settings_registry


def test_defaults_resolve_without_any_db_row(db):
    assert Setting.objects.count() == 0
    assert settings_registry.get("attendance.late_after_minutes") == 15
    assert settings_registry.get("payments.enforce_on_attendance") is False


def test_unknown_key_raises(db):
    with pytest.raises(KeyError):
        settings_registry.get("attendance.does_not_exist")


def test_override_takes_effect_immediately(db):
    settings_registry.set("attendance.late_after_minutes", 25)
    assert settings_registry.get("attendance.late_after_minutes") == 25
    assert Setting.objects.filter(key="attendance.late_after_minutes").exists()


def test_bool_coercion_from_form_values(db):
    settings_registry.set("payments.enforce_on_attendance", "true")
    assert settings_registry.get("payments.enforce_on_attendance") is True
    settings_registry.set("payments.enforce_on_attendance", "0")
    assert settings_registry.get("payments.enforce_on_attendance") is False


def test_choice_validation(db):
    with pytest.raises(ValidationError):
        settings_registry.set("attendance.not_assigned_policy", "WHATEVER")


def test_bounds_validation(db):
    with pytest.raises(ValidationError):
        settings_registry.set("attendance.late_after_minutes", -5)


def test_reset_restores_default(db):
    settings_registry.set("billing.due_day_of_month", 12)
    assert settings_registry.get("billing.due_day_of_month") == 12
    settings_registry.reset("billing.due_day_of_month")
    assert settings_registry.get("billing.due_day_of_month") == 5


def test_cache_invalidated_when_row_deleted_directly(db):
    settings_registry.set("payments.grace_days", 3)
    assert settings_registry.get("payments.grace_days") == 3
    Setting.objects.filter(key="payments.grace_days").delete()  # signal invalidates
    assert settings_registry.get("payments.grace_days") == 10


def test_every_spec_has_a_resolvable_value(db):
    values = settings_registry.all()
    from apps.core.policies import SPECS

    assert set(values) == {s.key for s in SPECS}
