"""TASK-090 — the feature catalogue is code, and a typo is always an error."""

import pytest

from apps.tenancy.exceptions import UnknownFeature
from apps.tenancy.features import (
    CORE_KEYS,
    FEATURE_KEYS,
    FEATURES,
    dependency_closure,
    grouped_specs,
    spec_for,
    specs_for_group,
)


def test_unknown_key_raises_rather_than_resolving_false():
    """A silent False would disable a feature nobody meant to disable, and the
    symptom (a missing menu entry) is a long way from the cause (a typo)."""
    with pytest.raises(UnknownFeature):
        spec_for("payments.teleport")


def test_unknown_feature_is_a_keyerror():
    """Matches policies.spec_for's contract, so both read the same at call sites."""
    assert issubclass(UnknownFeature, KeyError)


def test_keys_are_unique():
    keys = [spec.key for spec in FEATURES]
    assert len(keys) == len(set(keys))
    assert set(keys) == set(FEATURE_KEYS)


def test_every_dependency_exists():
    for spec in FEATURES:
        for dependency in spec.depends_on:
            assert dependency in FEATURE_KEYS, f"{spec.key} -> {dependency}"


def test_dependency_graph_is_acyclic():
    """The resolver walks depends_on transitively; a cycle would not terminate."""
    for spec in FEATURES:
        assert spec.key not in dependency_closure(spec.key)


def test_core_features_are_on_by_default():
    """An is_core feature that defaulted off would be permanently contradictory."""
    for spec in FEATURES:
        if spec.is_core:
            assert spec.default is True


def test_core_features_have_no_dependencies():
    """Core is the floor: it cannot be switched off by something above it."""
    for spec in FEATURES:
        if spec.is_core:
            assert spec.depends_on == ()


def test_core_keys_precomputed_correctly():
    assert CORE_KEYS == frozenset(s.key for s in FEATURES if s.is_core)
    assert "students" in CORE_KEYS
    assert "payments" not in CORE_KEYS


def test_dependency_closure_is_transitive():
    # reports.financial -> payments; payments has no parents of its own.
    assert dependency_closure("reports.financial") == frozenset({"payments"})
    assert dependency_closure("payments") == frozenset()
    assert dependency_closure("cards.bulk_import") == frozenset({"cards"})


def test_grouped_specs_covers_every_feature_once():
    seen = [spec.key for _group, _label, specs in grouped_specs() for spec in specs]
    assert sorted(seen) == sorted(FEATURE_KEYS)


def test_specs_for_group():
    payments = {spec.key for spec in specs_for_group("payments")}
    assert "payments" in payments
    assert "payments.refunds" in payments
    assert "students" not in payments


def test_money_features_depend_on_payments():
    """Switching payments off must take every money-shaped screen with it."""
    for key in (
        "payments.refunds",
        "payments.waivers",
        "payments.receipt_pdf",
        "reports.financial",
    ):
        assert "payments" in dependency_closure(key), key
