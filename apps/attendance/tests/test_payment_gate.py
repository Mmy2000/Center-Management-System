"""Case 31: the payment gate — the single, optional bridge (docs/04 §F.7)."""

from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.attendance import services
from apps.attendance.models import Attendance, AttendanceState, AttendanceStatus
from apps.attendance.services import ScanRejected
from apps.core.registry import settings_registry
from apps.payments import services as pay
from apps.payments.models import ChargeStatus, MonthlyCharge
from apps.students import assignment_services as assign_svc

from .conftest import make_lesson

pytestmark = pytest.mark.django_db


@pytest.fixture
def charge(world, assigned_student, user_factory):
    """An unpaid charge for this month, past the grace period."""
    student, card = assigned_student
    settings_registry.set("payments.grace_days", 0)
    month = timezone.localdate().replace(day=1)
    charge, _ = MonthlyCharge.objects.get_or_create(
        student=student,
        grade_subject=world["physics_sec3"],
        billing_month=month,
        defaults={"amount_due": Decimal("500.00"), "group": world["group_a"]},
    )
    return charge


def scan(lesson, card, **kwargs):
    return services.scan(lesson_id=lesson.pk, qr_token=card.qr_token, **kwargs)


def test_gate_is_off_by_default_so_unpaid_students_walk_in(assigned_student, lesson_a, charge):
    student, card = assigned_student
    assert settings_registry.get("payments.enforce_on_attendance") is False

    result = scan(lesson_a, card)
    assert result["code"] == "OK_CHECK_IN"
    assert "payment" not in result


def test_enforced_but_permissive_warns_and_still_admits(assigned_student, lesson_a, charge):
    student, card = assigned_student
    settings_registry.set("payments.enforce_on_attendance", True)
    settings_registry.set("payments.allow_unpaid_attendance", True)

    result = scan(lesson_a, card)
    assert result["code"] == "OK_CHECK_IN"
    assert "WARN_PAYMENT_DUE" in result["warnings"]
    assert result["payment"]["remaining"] == "500.00"
    assert Attendance.objects.count() == 1


def test_blocking_policy_refuses_the_scan(assigned_student, lesson_a, charge):
    student, card = assigned_student
    settings_registry.set("payments.enforce_on_attendance", True)
    settings_registry.set("payments.allow_unpaid_attendance", False)
    settings_registry.set("payments.unpaid_block_action", "BLOCK")

    with pytest.raises(ScanRejected) as exc:
        scan(lesson_a, card)
    assert exc.value.code == "ERR_PAYMENT_BLOCKED"
    assert Attendance.objects.count() == 0


def test_approval_policy_lets_a_supervisor_override(assigned_student, lesson_a, charge):
    student, card = assigned_student
    settings_registry.set("payments.enforce_on_attendance", True)
    settings_registry.set("payments.allow_unpaid_attendance", False)
    settings_registry.set("payments.unpaid_block_action", "REQUIRE_APPROVAL")

    with pytest.raises(ScanRejected) as exc:
        scan(lesson_a, card)
    assert exc.value.code == "NEEDS_APPROVAL_PAYMENT"

    result = scan(lesson_a, card, approve=True)
    assert result["code"] == "OK_CHECK_IN"


def test_grace_period_admits_everyone(assigned_student, lesson_a, charge):
    student, card = assigned_student
    settings_registry.set("payments.enforce_on_attendance", True)
    settings_registry.set("payments.allow_unpaid_attendance", False)
    settings_registry.set("payments.grace_days", 31)

    assert scan(lesson_a, card)["code"] == "OK_CHECK_IN"


def test_partial_payment_follows_its_own_policy(assigned_student, lesson_a, charge, user_factory):
    student, card = assigned_student
    cashier = user_factory(username="cash2")
    pay.record_payment(charge, "200.00", collected_by=cashier)
    charge.refresh_from_db()
    assert charge.status == ChargeStatus.PARTIALLY_PAID

    settings_registry.set("payments.enforce_on_attendance", True)
    settings_registry.set("payments.allow_unpaid_attendance", False)
    settings_registry.set("payments.allow_partial_attendance", True)

    result = scan(lesson_a, card)
    assert result["code"] == "OK_CHECK_IN"
    assert result["payment"]["remaining"] == "300.00"


def test_a_settled_charge_passes_silently(assigned_student, lesson_a, charge, user_factory):
    student, card = assigned_student
    pay.record_payment(charge, "500.00", collected_by=user_factory(username="cash3"))
    settings_registry.set("payments.enforce_on_attendance", True)
    settings_registry.set("payments.allow_unpaid_attendance", False)

    result = scan(lesson_a, card)
    assert result["code"] == "OK_CHECK_IN"
    assert result["warnings"] == []


def test_a_student_with_no_charge_yet_is_never_blocked(assigned_student, lesson_a):
    student, card = assigned_student
    settings_registry.set("payments.enforce_on_attendance", True)
    settings_registry.set("payments.allow_unpaid_attendance", False)

    assert scan(lesson_a, card)["code"] == "OK_CHECK_IN"


def test_debt_in_another_subject_does_not_block_this_lesson(
    world, assigned_student, lesson_a, user_factory
):
    """The gate is scoped to the lesson's own offering."""
    student, card = assigned_student
    assign_svc.assign_student(student, world["chem_a"])
    MonthlyCharge.objects.create(
        student=student,
        grade_subject=world["chemistry_sec3"],
        billing_month=timezone.localdate().replace(day=1),
        amount_due=Decimal("450.00"),
    )
    settings_registry.set("payments.enforce_on_attendance", True)
    settings_registry.set("payments.allow_unpaid_attendance", False)
    settings_registry.set("payments.grace_days", 0)

    assert scan(lesson_a, card)["code"] == "OK_CHECK_IN"


def test_attendance_and_payment_stay_independent(world, assigned_student, charge, user_factory):
    """All six combinations of docs/04 §F.7 are representable."""
    student, card = assigned_student
    cashier = user_factory(username="cash4")

    # PAID + ABSENT
    pay.record_payment(charge, "500.00", collected_by=cashier)
    lesson = make_lesson(world["group_a"])
    services.finalize_lesson(lesson)

    charge.refresh_from_db()
    absence = Attendance.objects.get(lesson=lesson, student=student)
    assert charge.status == ChargeStatus.PAID
    assert absence.state == AttendanceState.ABSENT

    # Marking attendance must not touch the charge...
    other = make_lesson(world["group_a"], start=timezone.now())
    services.scan(lesson_id=other.pk, qr_token=card.qr_token)
    charge.refresh_from_db()
    assert charge.status == ChargeStatus.PAID
    assert charge.total_paid == Decimal("500.00")

    # ...and refunding must not touch attendance.
    payment = charge.payments.first()
    pay.refund_payment(payment, actor=cashier, reason="انسحاب")
    assert Attendance.objects.get(lesson=other, student=student).status == AttendanceStatus.PRESENT


def test_no_foreign_key_exists_between_attendance_and_money():
    """Principle 3 as a schema guard."""
    attendance_targets = {
        field.related_model.__name__
        for field in Attendance._meta.get_fields()
        if field.is_relation and field.related_model
    }
    assert "MonthlyCharge" not in attendance_targets
    assert "Payment" not in attendance_targets

    charge_targets = {
        field.related_model.__name__
        for field in MonthlyCharge._meta.get_fields()
        if field.is_relation and field.related_model
    }
    assert "Attendance" not in charge_targets


def test_billing_month_normalisation():
    assert pay.normalize_month("2026-08") == date(2026, 8, 1)
    assert pay.normalize_month(date(2026, 8, 31)) == date(2026, 8, 1)
