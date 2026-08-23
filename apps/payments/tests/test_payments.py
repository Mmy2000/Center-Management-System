"""Payment cases 23–31 from docs/06 §K.2."""

import json
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import EducationalStage, Grade, GradeSubject, Group, Subject
from apps.accounts.models import Role
from apps.core.http import DomainError
from apps.core.models import AuditAction, AuditLog
from apps.core.registry import settings_registry
from apps.payments import services
from apps.payments.models import ChargeStatus, MonthlyCharge, Payment, PaymentKind
from apps.students import assignment_services as assign_svc
from apps.students.services import create_student

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"
MONTH = date(2026, 8, 1)


@pytest.fixture
def world(db):
    stage = EducationalStage.objects.create(name="Secondary", code="SEC")
    grade = Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3")
    physics = Subject.objects.create(name="Physics", code="PHY")
    maths = Subject.objects.create(name="Mathematics", code="MATH")
    physics_sec3 = GradeSubject.objects.create(
        grade=grade, subject=physics, default_monthly_fee=Decimal("500.00")
    )
    maths_sec3 = GradeSubject.objects.create(
        grade=grade, subject=maths, default_monthly_fee=Decimal("400.00")
    )
    return {
        "grade": grade,
        "physics_sec3": physics_sec3,
        "maths_sec3": maths_sec3,
        "phys_a": Group.objects.create(
            grade_subject=physics_sec3,
            name="Group A",
            code="SEC3-PHY-A",
            monthly_fee=Decimal("500.00"),
        ),
        "phys_b": Group.objects.create(
            grade_subject=physics_sec3,
            name="Group B",
            code="SEC3-PHY-B",
            monthly_fee=Decimal("500.00"),
        ),
        "math_c": Group.objects.create(
            grade_subject=maths_sec3,
            name="Group C",
            code="SEC3-MATH-C",
            monthly_fee=Decimal("400.00"),
        ),
    }


@pytest.fixture
def student(world):
    return create_student(full_name="أحمد محمد", grade=world["grade"], guardian_phone="01012345678")


@pytest.fixture
def cashier(user_factory):
    call_command("seed_roles", verbosity=0)
    return user_factory(username="cash", role=Role.CASHIER, password=PASSWORD)


@pytest.fixture
def charge(world, student, cashier):
    assign_svc.assign_student(student, world["phys_a"])
    services.generate_monthly_charges(MONTH)
    return MonthlyCharge.objects.get(student=student, grade_subject=world["physics_sec3"])


@pytest.fixture
def admin_client_(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _patch(client, url, payload):
    return client.patch(url, data=json.dumps(payload), content_type="application/json")


# ------------------------------------------------------------- generation 27 #


def test_27_generation_is_idempotent(world, student):
    assign_svc.assign_student(student, world["phys_a"])
    first = services.generate_monthly_charges(MONTH)
    second = services.generate_monthly_charges(MONTH)

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["existing"] == 1
    assert MonthlyCharge.objects.count() == 1


def test_duplicate_charge_is_impossible_at_the_database_level(world, student, charge):
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            MonthlyCharge.objects.create(
                student=student,
                grade_subject=world["physics_sec3"],
                billing_month=MONTH,
                amount_due=Decimal("500.00"),
            )


def test_28_the_fee_is_a_snapshot(world, student, charge):
    world["phys_a"].monthly_fee = Decimal("700.00")
    world["phys_a"].save()

    charge.refresh_from_db()
    assert charge.amount_due == Decimal("500.00")

    services.generate_monthly_charges(MONTH)  # re-running changes nothing
    charge.refresh_from_db()
    assert charge.amount_due == Decimal("500.00")


def test_multi_subject_student_gets_one_charge_per_subject(world, student):
    assign_svc.assign_student(student, world["phys_a"])
    assign_svc.assign_student(student, world["math_c"])
    services.generate_monthly_charges(MONTH)

    charges = MonthlyCharge.objects.filter(student=student)
    assert charges.count() == 2
    assert sum(c.amount_due for c in charges) == Decimal("900.00")


def test_transfer_mid_month_does_not_duplicate_or_reprice(world, student, charge):
    assignment = student.assignments.get()
    assign_svc.transfer_student(assignment, world["phys_b"], reason="تعارض مواعيد")
    services.generate_monthly_charges(MONTH)

    charges = MonthlyCharge.objects.filter(student=student)
    assert charges.count() == 1
    assert charges.get().amount_due == Decimal("500.00")


def test_ended_assignments_are_not_billed_for_later_months(world, student):
    assignment, _ = assign_svc.assign_student(student, world["phys_a"], start_date=date(2026, 6, 1))
    assign_svc.end_assignment(assignment, end_date=date(2026, 7, 31), reason="LEFT_CENTER")

    summary = services.generate_monthly_charges(MONTH)
    assert summary["created"] == 0


def test_dry_run_writes_nothing(world, student):
    assign_svc.assign_student(student, world["phys_a"])
    summary = services.generate_monthly_charges(MONTH, dry_run=True)
    assert summary["created"] == 1
    assert MonthlyCharge.objects.count() == 0


def test_63_mid_month_joiner_is_billed_immediately(world, student):
    """Auto-charge only fires once the month has been generated."""
    other = create_student(full_name="سارة علي", grade=world["grade"], guardian_phone="01112345678")
    assign_svc.assign_student(other, world["phys_a"])
    services.generate_monthly_charges(timezone.localdate())

    assign_svc.assign_student(student, world["math_c"])
    assert MonthlyCharge.objects.filter(
        student=student, billing_month=timezone.localdate().replace(day=1)
    ).exists()


# ----------------------------------------------------------- collection 23–26 #


def test_23_three_partial_payments_walk_the_status(charge, cashier):
    assert charge.status == ChargeStatus.UNPAID

    services.record_payment(charge, "200.00", collected_by=cashier)
    charge.refresh_from_db()
    assert charge.status == ChargeStatus.PARTIALLY_PAID
    assert charge.balance == Decimal("300.00")

    services.record_payment(charge, "150.00", collected_by=cashier)
    charge.refresh_from_db()
    assert charge.total_paid == Decimal("350.00")

    services.record_payment(charge, "150.00", collected_by=cashier)
    charge.refresh_from_db()
    assert charge.status == ChargeStatus.PAID
    assert charge.balance == Decimal("0.00")
    assert charge.payments.count() == 3


def test_24_overpayment_is_rejected_unless_allowed(charge, cashier):
    with pytest.raises(DomainError) as exc:
        services.record_payment(charge, "600.00", collected_by=cashier)
    assert exc.value.code == "ERR_OVERPAYMENT"
    charge.refresh_from_db()
    assert charge.total_paid == Decimal("0.00")

    settings_registry.set("payments.allow_overpayment", True)
    services.record_payment(charge, "600.00", collected_by=cashier)
    charge.refresh_from_db()
    assert charge.status == ChargeStatus.OVERPAID


def test_26_non_positive_amounts_are_refused(charge, cashier):
    for amount in ("0", "-50"):
        with pytest.raises(DomainError) as exc:
            services.record_payment(charge, amount, collected_by=cashier)
        assert exc.value.code == "ERR_VALIDATION"

    with transaction.atomic():
        with pytest.raises(IntegrityError):
            Payment.objects.create(
                monthly_charge=charge,
                student=charge.student,
                amount=Decimal("-1.00"),
                paid_at=timezone.now(),
                receipt_number="R-BAD",
                collected_by=cashier,
            )


def test_receipt_numbers_are_unique_and_sequential(charge, cashier):
    first = services.record_payment(charge, "100.00", collected_by=cashier)
    second = services.record_payment(charge, "100.00", collected_by=cashier)
    assert first.receipt_number == "R-000001"
    assert second.receipt_number == "R-000002"


def test_payments_are_immutable(charge, cashier):
    payment = services.record_payment(charge, "100.00", collected_by=cashier)

    payment.amount = Decimal("999.00")
    with pytest.raises(ValidationError):
        payment.save()

    with pytest.raises(ValidationError):
        payment.delete()

    # ...but a note may still be appended.
    payment.refresh_from_db()
    payment.notes = "تم التأكيد"
    payment.save(update_fields=["notes"])


def test_25_refund_reduces_paid_and_leaves_the_original_row_intact(charge, cashier):
    payment = services.record_payment(charge, "500.00", collected_by=cashier)
    charge.refresh_from_db()
    assert charge.status == ChargeStatus.PAID
    before = Payment.objects.filter(pk=payment.pk).values().first()

    services.refund_payment(payment, actor=cashier, reason="انسحب الطالب")

    charge.refresh_from_db()
    assert charge.total_paid == Decimal("0.00")
    assert charge.status == ChargeStatus.UNPAID
    assert Payment.objects.count() == 2

    after = Payment.objects.filter(pk=payment.pk).values().first()
    assert {k: v for k, v in before.items() if k != "updated_at"} == {
        k: v for k, v in after.items() if k != "updated_at"
    }

    refund = Payment.objects.get(kind=PaymentKind.REFUND)
    assert refund.reverses_id == payment.pk
    assert AuditLog.objects.filter(action=AuditAction.PAYMENT_REFUNDED).exists()


def test_refund_requires_a_reason_and_cannot_exceed_the_paid_total(charge, cashier):
    payment = services.record_payment(charge, "200.00", collected_by=cashier)

    with pytest.raises(DomainError) as exc:
        services.refund_payment(payment, actor=cashier, reason="")
    assert exc.value.code == "ERR_REASON_REQUIRED"

    with pytest.raises(DomainError) as exc:
        services.refund_payment(payment, "300.00", actor=cashier, reason="خطأ")
    assert exc.value.code == "ERR_REFUND_EXCEEDS_PAID"


# ---------------------------------------------------------- waive / cancel 30 #


def test_30_waived_and_cancelled_are_excluded_from_expected_revenue(world, student, cashier):
    assign_svc.assign_student(student, world["phys_a"])
    other = create_student(full_name="سارة علي", grade=world["grade"], guardian_phone="01112345678")
    assign_svc.assign_student(other, world["math_c"])
    services.generate_monthly_charges(MONTH)

    physics = MonthlyCharge.objects.get(student=student)
    maths = MonthlyCharge.objects.get(student=other)
    services.waive_charge(physics, actor=cashier, reason="ابن مدرّس")
    services.cancel_charge(maths, actor=cashier, reason="إلغاء اشتراك")

    from django.db.models import Sum

    billable = MonthlyCharge.objects.billable().aggregate(total=Sum("amount_due"))["total"]
    assert billable is None
    assert MonthlyCharge.objects.outstanding().count() == 0


def test_waiving_is_sticky_against_derivation(charge, cashier):
    services.waive_charge(charge, actor=cashier, reason="منحة")
    charge.refresh_from_db()
    assert services.recalculate_status(charge) == ChargeStatus.WAIVED


def test_cannot_collect_on_a_waived_charge(charge, cashier):
    services.waive_charge(charge, actor=cashier, reason="منحة")
    charge.refresh_from_db()
    with pytest.raises(DomainError) as exc:
        services.record_payment(charge, "100.00", collected_by=cashier)
    assert exc.value.code == "ERR_CHARGE_CLOSED"


def test_cannot_cancel_a_charge_that_has_payments(charge, cashier):
    services.record_payment(charge, "100.00", collected_by=cashier)
    charge.refresh_from_db()
    with pytest.raises(DomainError) as exc:
        services.cancel_charge(charge, actor=cashier, reason="خطأ")
    assert exc.value.code == "ERR_CHARGE_HAS_PAYMENTS"


def test_discount_changes_the_balance_and_status(charge, cashier):
    services.record_payment(charge, "400.00", collected_by=cashier)
    charge.refresh_from_db()
    assert charge.status == ChargeStatus.PARTIALLY_PAID

    services.update_charge(charge, actor=cashier, reason="خصم أخوة", discount_amount="100.00")
    charge.refresh_from_db()
    assert charge.balance == Decimal("0.00")
    assert charge.status == ChargeStatus.PAID


def test_discount_cannot_exceed_the_amount_due(charge, cashier):
    with pytest.raises(DomainError):
        services.update_charge(charge, actor=cashier, discount_amount="900.00")


# -------------------------------------------------------------------- drift #


def test_drift_is_detected_and_repaired(charge, cashier):
    services.record_payment(charge, "300.00", collected_by=cashier)
    MonthlyCharge.objects.filter(pk=charge.pk).update(total_paid=Decimal("999.00"))

    result = services.recalculate_charges(MONTH)
    assert len(result["drift"]) == 1

    charge.refresh_from_db()
    assert charge.total_paid == Decimal("300.00")
    assert charge.status == ChargeStatus.PARTIALLY_PAID
    assert services.recalculate_charges(MONTH)["drift"] == []


def test_balance_is_computed_by_the_database(charge, cashier):
    services.record_payment(charge, "120.00", collected_by=cashier)
    charge.refresh_from_db()
    assert charge.balance == charge.amount_due - charge.discount_amount - charge.total_paid


# ----------------------------------------------------------------------- API #


def test_collect_endpoint_returns_a_receipt(admin_client_, charge):
    response = _post(
        admin_client_,
        reverse("payments_api:payments"),
        {"charge_id": charge.pk, "amount": "200.00", "method": "CASH"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["payment"]["receipt_number"].startswith("R-")
    assert data["charge"]["balance"] == "300.00"

    receipt = admin_client_.get(reverse("payments_api:receipt", args=[data["payment"]["id"]]))
    assert receipt.status_code == 200
    assert receipt["Content-Type"] == "application/pdf"
    assert receipt["Content-Disposition"].startswith("attachment;")
    assert data["payment"]["receipt_number"] in receipt["Content-Disposition"]
    assert receipt.content[:5] == b"%PDF-"
    assert len(receipt.content) > 1500


def test_overpayment_is_a_field_error_at_the_api(admin_client_, charge):
    response = _post(
        admin_client_,
        reverse("payments_api:payments"),
        {"charge_id": charge.pk, "amount": "5000"},
    )
    assert response.status_code == 409
    assert "amount" in response.json()["field_errors"]


def test_charge_list_totals_reconcile_with_the_ledger(admin_client_, charge, cashier):
    services.record_payment(charge, "200.00", collected_by=cashier)
    data = admin_client_.get(reverse("payments_api:charges") + "?month=2026-08").json()["data"]

    assert data["totals"]["expected"] == "500.00"
    assert data["totals"]["collected"] == "200.00"
    assert data["totals"]["outstanding"] == "300.00"
    assert data["totals"]["partial_count"] == 1


def test_daybook_totals_match_the_transactions(admin_client_, charge, cashier):
    services.record_payment(charge, "200.00", collected_by=cashier)
    payment = services.record_payment(charge, "100.00", collected_by=cashier)
    services.refund_payment(payment, actor=cashier, reason="خطأ إدخال")

    data = admin_client_.get(reverse("payments_api:payments")).json()["data"]
    assert data["totals"]["collected"] == "300.00"
    assert data["totals"]["refunded"] == "100.00"
    assert data["totals"]["net"] == "200.00"


def test_student_financial_summary(admin_client_, world, student, cashier):
    assign_svc.assign_student(student, world["phys_a"])
    assign_svc.assign_student(student, world["math_c"])
    services.generate_monthly_charges(MONTH)
    physics = MonthlyCharge.objects.get(student=student, grade_subject=world["physics_sec3"])
    services.record_payment(physics, "300.00", collected_by=cashier)

    data = admin_client_.get(
        reverse("payments_api:student_financial_summary", args=[student.pk]) + "?month=2026-08"
    ).json()["data"]

    assert data["totals"]["due"] == "900.00"
    assert data["totals"]["paid"] == "300.00"
    assert data["totals"]["balance"] == "600.00"


def test_cashier_can_collect_but_not_refund(client, charge, cashier):
    client.login(username="cash", password=PASSWORD)
    payment = _post(
        client,
        reverse("payments_api:payments"),
        {"charge_id": charge.pk, "amount": "100.00"},
    )
    assert payment.status_code == 200

    refund = _post(
        client,
        reverse("payments_api:refund", args=[payment.json()["data"]["payment"]["id"]]),
        {"reason": "خطأ"},
    )
    assert refund.status_code == 403


def test_scan_operator_cannot_see_money(client, user_factory, charge):
    call_command("seed_roles", verbosity=0)
    user_factory(username="op", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op", password=PASSWORD)
    assert client.get(reverse("payments_api:charges")).status_code == 403
    assert client.get(reverse("payments:workspace")).status_code == 403


def test_workspace_page_renders(admin_client_):
    assert admin_client_.get(reverse("payments:workspace")).status_code == 200


# ------------------------------------------------------------ PDF receipts #


def test_receipt_pdf_carries_the_center_identity(admin_client_, charge, cashier):
    from apps.core.registry import settings_registry

    settings_registry.set("center.name", "سنتر النجاح")
    payment = services.record_payment(charge, "150.00", collected_by=cashier)

    response = admin_client_.get(reverse("payments_api:receipt", args=[payment.pk]))
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert b"Amiri" in response.content
    assert payment.receipt_number in response["Content-Disposition"]


def test_receipt_shows_the_number_the_amount_and_the_balance(admin_client_, charge, cashier):
    import io

    from pypdf import PdfReader

    payment = services.record_payment(charge, "200.00", collected_by=cashier)
    response = admin_client_.get(reverse("payments_api:receipt", args=[payment.pk]))

    page = PdfReader(io.BytesIO(response.content)).pages[0]
    text = page.extract_text(extraction_mode="layout")
    assert payment.receipt_number in text
    assert "200.00" in text  # amount paid
    assert "300.00" in text  # balance left


def test_thermal_receipt_is_narrower_but_still_valid(admin_client_, charge, cashier):
    payment = services.record_payment(charge, "150.00", collected_by=cashier)
    a5 = admin_client_.get(reverse("payments_api:receipt", args=[payment.pk]))
    thermal = admin_client_.get(
        reverse("payments_api:receipt", args=[payment.pk]) + "?size=thermal"
    )
    assert thermal.status_code == 200
    assert thermal.content.startswith(b"%PDF-")
    assert a5.content != thermal.content


def test_refund_receipt_renders(admin_client_, charge, cashier):
    payment = services.record_payment(charge, "150.00", collected_by=cashier)
    refund = services.refund_payment(payment, actor=cashier, reason="خطأ إدخال")

    response = admin_client_.get(reverse("payments_api:receipt", args=[refund.pk]))
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


def test_receipt_requires_permission(client, user_factory, charge, cashier):
    payment = services.record_payment(charge, "100.00", collected_by=cashier)
    user_factory(username="op9", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op9", password=PASSWORD)
    assert client.get(reverse("payments_api:receipt", args=[payment.pk])).status_code == 403
