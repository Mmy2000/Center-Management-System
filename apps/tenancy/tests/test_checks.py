"""TASK-093 — the check that covers the model somebody adds next month.

The leak suite proves isolation for the models that exist today. This proves the
*next* model cannot quietly skip it.
"""

import pytest
from django.db import models
from django.test.utils import isolate_apps

from apps.tenancy import checks
from apps.tenancy.base import TenantOwnedModel
from apps.tenancy.checks import (
    EXEMPT_MODELS,
    PENDING_MIGRATION,
    check_feature_catalogue,
    check_models_are_tenant_scoped,
)


def _ids(issues):
    return sorted({issue.id for issue in issues})


def test_current_state_has_no_errors():
    """Every model is owned, exempt, or a known Phase 14 item — none forgotten."""
    issues = check_models_are_tenant_scoped()
    assert all(issue.id == "tenancy.W001" for issue in issues), _ids(issues)


def test_pending_list_matches_the_warnings():
    warned = {
        issue.obj._meta.label_lower
        for issue in check_models_are_tenant_scoped()
        if issue.id == "tenancy.W001"
    }
    assert warned == set(PENDING_MIGRATION)


def test_pending_and_exempt_do_not_overlap():
    """A model is retrofitted or excused — deciding both ways hides the truth."""
    assert set(PENDING_MIGRATION) & set(EXEMPT_MODELS) == set()


def test_every_exemption_carries_a_reason():
    for label, reason in EXEMPT_MODELS.items():
        assert reason.strip(), f"{label} is exempt with no reason given"


def test_every_pending_entry_names_its_task():
    for label, task in PENDING_MIGRATION.items():
        assert task.startswith("TASK-"), f"{label} -> {task!r}"


@isolate_apps("apps.students")
def test_a_new_unowned_model_is_an_error(monkeypatch):
    """The case that matters: somebody adds a model and forgets."""

    class Forgotten(models.Model):  # noqa: DJ008 - never instantiated
        class Meta:
            app_label = "students"

    monkeypatch.setattr(checks.django_apps, "get_models", lambda *a, **k: [Forgotten])
    issues = check_models_are_tenant_scoped()
    assert _ids(issues) == ["tenancy.E001"]
    assert "not tenant-scoped" in issues[0].msg


@isolate_apps("django.contrib.auth")
def test_a_model_outside_the_domain_apps_is_ignored(monkeypatch):
    class ThirdParty(models.Model):  # noqa: DJ008 - never instantiated
        class Meta:
            app_label = "auth"

    monkeypatch.setattr(checks.django_apps, "get_models", lambda *a, **k: [ThirdParty])
    assert check_models_are_tenant_scoped() == []


@isolate_apps("apps.students")
def test_a_retrofitted_model_still_listed_as_pending_is_an_error(monkeypatch):
    """Each Phase 14 task must delete its own line; this is what makes it."""

    class Retrofitted(TenantOwnedModel):
        class Meta:
            app_label = "students"

    monkeypatch.setattr(checks.django_apps, "get_models", lambda *a, **k: [Retrofitted])
    monkeypatch.setitem(PENDING_MIGRATION, "students.retrofitted", "TASK-097")

    issues = check_models_are_tenant_scoped()
    assert _ids(issues) == ["tenancy.E002"]
    assert "PENDING_MIGRATION" in issues[0].msg


def test_feature_catalogue_check_is_clean():
    assert check_feature_catalogue() == []


def test_feature_catalogue_check_catches_an_unknown_dependency(monkeypatch):
    from apps.tenancy import features
    from apps.tenancy.features import FeatureSpec

    broken = FeatureSpec("x.broken", "x", "core", True, depends_on=("does.not.exist",))
    monkeypatch.setattr(features, "FEATURES", (broken,))
    monkeypatch.setattr(features, "FEATURE_KEYS", frozenset({"x.broken"}))

    issues = check_feature_catalogue()
    assert _ids(issues) == ["tenancy.E003"]


@pytest.mark.django_db
def test_manage_py_check_reports_no_errors():
    """The real thing, through Django's own machinery."""
    from django.core.checks import Error, run_checks

    errors = [issue for issue in run_checks() if isinstance(issue, Error)]
    tenancy_errors = [error for error in errors if str(error.id).startswith("tenancy.")]
    assert tenancy_errors == []
