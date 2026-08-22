"""Billing and collection services (docs/04, TASK-062 → 066).

The money rules that matter:
  * a charge's fee is a *snapshot* — raising a group's fee never rewrites it;
  * charge generation is idempotent (``get_or_create`` on the unique key);
  * every payment takes a row lock and uses F() arithmetic, so two cashiers on
    the same charge can neither lose an update nor both pass the overpayment
    check;
  * status is derived, never typed;
  * payments are immutable — mistakes are corrected with a REFUND.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import diff, record, snapshot
from apps.core.http import DomainError
from apps.core.models import AuditAction
from apps.core.registry import settings_registry
from apps.core.sequences import format_code, next_number

from .models import ChargeStatus, GeneratedBy, MonthlyCharge, Payment, PaymentKind

ZERO = Decimal("0.00")
RECEIPT_KEY = "receipt_number"
RECEIPT_PREFIX = "R-"

AUDITED_CHARGE_FIELDS = ["amount_due", "discount_amount", "due_date", "status", "group"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def normalize_month(value) -> date:
    if isinstance(value, str):
        parts = value.split("-")
        try:
            value = date(int(parts[0]), int(parts[1]), 1)
        except (ValueError, IndexError) as exc:
            raise DomainError("ERR_VALIDATION", _("صيغة الشهر غير صحيحة (YYYY-MM)")) from exc
    return value.replace(day=1)


def month_end(billing_month: date) -> date:
    return billing_month.replace(day=monthrange(billing_month.year, billing_month.month)[1])


def next_receipt_number() -> str:
    return format_code(RECEIPT_PREFIX, next_number(RECEIPT_KEY))


def recalculate_status(charge: MonthlyCharge, *, save=True) -> str:
    """Status is *derived* — this function is its only author (docs/04 §F.4)."""
    if charge.status in ChargeStatus.sticky():
        return charge.status

    net = charge.amount_due - charge.discount_amount
    paid = charge.total_paid
    if paid <= 0:
        status = ChargeStatus.UNPAID
    elif paid < net:
        status = ChargeStatus.PARTIALLY_PAID
    elif paid == net:
        status = ChargeStatus.PAID
    else:
        status = ChargeStatus.OVERPAID

    if status != charge.status:
        charge.status = status
        if save:
            charge.save(update_fields=["status", "updated_at"])
    return status


def ledger_total(charge: MonthlyCharge) -> Decimal:
    """Σ PAYMENT − Σ REFUND, straight from the ledger."""
    rows = charge.payments.values("kind").annotate(total=Sum("amount"))
    totals = {row["kind"]: row["total"] or ZERO for row in rows}
    return totals.get(PaymentKind.PAYMENT, ZERO) - totals.get(PaymentKind.REFUND, ZERO)


# --------------------------------------------------------------------------- #
# Charge generation (TASK-062)
# --------------------------------------------------------------------------- #

def _due_date(billing_month: date) -> date:
    day = settings_registry.get("billing.due_day_of_month")
    return billing_month + timedelta(days=day - 1)


@transaction.atomic
def create_charge_for_assignment(assignment, billing_month: date, *, actor=None, generated="AUTO"):
    """One charge per (student, offering, month). Fee snapshotted from the group."""
    billing_month = normalize_month(billing_month)
    charge, created = MonthlyCharge.objects.get_or_create(
        student_id=assignment.student_id,
        grade_subject_id=assignment.grade_subject_id,
        billing_month=billing_month,
        defaults={
            "group_id": assignment.group_id,
            "amount_due": assignment.group.monthly_fee,  # snapshot (docs/01 A3)
            "due_date": _due_date(billing_month),
            "generated_by": generated,
            "created_by": actor,
        },
    )
    if created:
        record(
            AuditAction.CHARGE_CREATED,
            charge,
            changes={
                "amount_due": str(charge.amount_due),
                "billing_month": billing_month.isoformat(),
                "group": str(assignment.group),
            },
            actor=actor,
        )
    return charge, created


def generate_monthly_charges(
    billing_month, *, actor=None, group=None, dry_run=False, verbosity=1
) -> dict:
    """Bill every active assignment overlapping the month. Idempotent."""
    from apps.students.models import AssignmentStatus, StudentGroupAssignment

    billing_month = normalize_month(billing_month)
    start, end = billing_month, month_end(billing_month)

    assignments = (
        StudentGroupAssignment.objects.filter(status=AssignmentStatus.ACTIVE, start_date__lte=end)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=start))
        .select_related("group", "student")
    )
    if group is not None:
        assignments = assignments.filter(group=group)

    created, existing, total = 0, 0, ZERO
    per_group: dict[str, int] = {}

    for assignment in assignments:
        total += assignment.group.monthly_fee
        per_group[assignment.group.code] = per_group.get(assignment.group.code, 0) + 1
        if dry_run:
            already = MonthlyCharge.objects.filter(
                student_id=assignment.student_id,
                grade_subject_id=assignment.grade_subject_id,
                billing_month=billing_month,
            ).exists()
            existing += 1 if already else 0
            created += 0 if already else 1
            continue

        _charge, was_created = create_charge_for_assignment(
            assignment, billing_month, actor=actor
        )
        created += 1 if was_created else 0
        existing += 0 if was_created else 1

    summary = {
        "billing_month": billing_month.isoformat(),
        "created": created,
        "existing": existing,
        "students": assignments.count(),
        "total_amount": str(total),
        "per_group": per_group,
        "dry_run": dry_run,
    }
    if not dry_run and created:
        record(
            AuditAction.CHARGE_CREATED,
            changes=summary,
            reason=f"generate_charges {billing_month:%Y-%m}",
            object_repr=f"{created} charges {billing_month:%Y-%m}",
            actor=actor,
        )
    return summary


# --------------------------------------------------------------------------- #
# Collection (TASK-064)
# --------------------------------------------------------------------------- #

@transaction.atomic
def record_payment(
    charge: MonthlyCharge,
    amount,
    *,
    collected_by,
    method="CASH",
    paid_at=None,
    reference="",
    notes="",
    kind=PaymentKind.PAYMENT,
    reverses=None,
) -> Payment:
    """Post one ledger line and refresh the charge, under a row lock."""
    amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    if amount <= ZERO:
        raise DomainError(
            "ERR_VALIDATION",
            _("المبلغ يجب أن يكون أكبر من صفر"),
            field_errors={"amount": [_("المبلغ يجب أن يكون أكبر من صفر")]},
        )
    if collected_by is None:
        raise DomainError("ERR_VALIDATION", _("يجب تحديد المُحصِّل"))

    # The lock serialises concurrent cashiers; F() keeps the arithmetic in SQL.
    charge = MonthlyCharge.objects.select_for_update().get(pk=charge.pk)

    if charge.status in ChargeStatus.sticky():
        raise DomainError(
            "ERR_CHARGE_CLOSED",
            _("لا يمكن التحصيل على رسوم معفاة أو ملغاة"),
            status=409,
        )

    if kind == PaymentKind.PAYMENT:
        if not settings_registry.get("payments.allow_overpayment") and amount > charge.balance:
            raise DomainError(
                "ERR_OVERPAYMENT",
                _("المبلغ أكبر من المتبقي (%(rem)s)") % {"rem": charge.balance},
                status=409,
                field_errors={"amount": [_("المبلغ أكبر من المتبقي")]},
            )
    else:
        if amount > charge.total_paid:
            raise DomainError(
                "ERR_REFUND_EXCEEDS_PAID",
                _("قيمة الاسترداد أكبر من المحصّل"),
                status=409,
                field_errors={"amount": [_("أكبر من المبلغ المحصّل")]},
            )

    payment = Payment.objects.create(
        monthly_charge=charge,
        student_id=charge.student_id,
        kind=kind,
        amount=amount,
        paid_at=paid_at or timezone.now(),
        method=method,
        receipt_number=next_receipt_number(),
        reference=reference,
        collected_by=collected_by,
        reverses=reverses,
        notes=notes,
    )

    delta = amount if kind == PaymentKind.PAYMENT else -amount
    MonthlyCharge.objects.filter(pk=charge.pk).update(total_paid=F("total_paid") + delta)
    charge.refresh_from_db(fields=["total_paid", "balance", "status"])
    recalculate_status(charge)

    record(
        AuditAction.PAYMENT_CREATED if kind == PaymentKind.PAYMENT else AuditAction.PAYMENT_REFUNDED,
        payment,
        changes={
            "amount": str(amount),
            "kind": kind,
            "charge": str(charge),
            "total_paid": str(charge.total_paid),
            "balance": str(charge.balance),
            "status": charge.status,
        },
        reason=notes,
        actor=collected_by,
    )
    return payment


@transaction.atomic
def refund_payment(payment: Payment, amount=None, *, actor, reason: str) -> Payment:
    """Correct a mistake without mutating history."""
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب الاسترداد مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    if payment.kind == PaymentKind.REFUND:
        raise DomainError("ERR_VALIDATION", _("لا يمكن استرداد حركة استرداد"), status=409)

    return record_payment(
        payment.monthly_charge,
        amount if amount is not None else payment.amount,
        collected_by=actor,
        method=payment.method,
        notes=reason,
        kind=PaymentKind.REFUND,
        reverses=payment,
    )


# --------------------------------------------------------------------------- #
# Administrative changes (TASK-066)
# --------------------------------------------------------------------------- #

@transaction.atomic
def update_charge(charge: MonthlyCharge, *, actor, reason: str = "", **fields) -> MonthlyCharge:
    """Discount / due-date / amount corrections, audited and re-derived."""
    before = snapshot(charge, AUDITED_CHARGE_FIELDS)
    for name in ("amount_due", "discount_amount"):
        if name in fields and fields[name] is not None:
            setattr(charge, name, Decimal(str(fields[name])).quantize(Decimal("0.01")))
    if fields.get("due_date"):
        charge.due_date = fields["due_date"]

    if charge.discount_amount > charge.amount_due:
        raise DomainError(
            "ERR_VALIDATION",
            _("الخصم أكبر من المستحق"),
            field_errors={"discount_amount": [_("أكبر من المستحق")]},
        )

    charge.updated_by = actor
    charge.save()
    charge.refresh_from_db(fields=["balance"])
    recalculate_status(charge)

    changes = diff(before, snapshot(charge, AUDITED_CHARGE_FIELDS))
    if changes:
        record(AuditAction.CHARGE_MODIFIED, charge, changes=changes, reason=reason, actor=actor)
    return charge


@transaction.atomic
def waive_charge(charge: MonthlyCharge, *, actor, reason: str) -> MonthlyCharge:
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب الإعفاء مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    previous = charge.status
    charge.status = ChargeStatus.WAIVED
    charge.waived_reason = reason
    charge.updated_by = actor
    charge.save(update_fields=["status", "waived_reason", "updated_by", "updated_at"])

    record(
        AuditAction.CHARGE_WAIVED,
        charge,
        changes={"status": {"old": previous, "new": ChargeStatus.WAIVED}},
        reason=reason,
        actor=actor,
    )
    return charge


@transaction.atomic
def cancel_charge(charge: MonthlyCharge, *, actor, reason: str) -> MonthlyCharge:
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب الإلغاء مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    if charge.total_paid > ZERO:
        raise DomainError(
            "ERR_CHARGE_HAS_PAYMENTS",
            _("لا يمكن إلغاء رسوم بها مدفوعات — نفّذ استردادًا أولًا"),
            status=409,
        )

    previous = charge.status
    charge.status = ChargeStatus.CANCELLED
    charge.updated_by = actor
    charge.save(update_fields=["status", "updated_by", "updated_at"])

    record(
        AuditAction.CHARGE_CANCELLED,
        charge,
        changes={"status": {"old": previous, "new": ChargeStatus.CANCELLED}},
        reason=reason,
        actor=actor,
    )
    return charge


# --------------------------------------------------------------------------- #
# Drift check (TASK-065)
# --------------------------------------------------------------------------- #

def recalculate_charges(billing_month=None, *, fix=True) -> dict:
    """Re-derive ``total_paid`` and ``status`` from the ledger.

    Drift should always be zero; if it isn't, something bypassed the service
    layer and that is a bug worth an alert.
    """
    qs = MonthlyCharge.objects.all()
    if billing_month:
        qs = qs.filter(billing_month=normalize_month(billing_month))

    drift = []
    for charge in qs.iterator():
        expected = ledger_total(charge)
        if expected != charge.total_paid:
            drift.append(
                {
                    "charge": charge.pk,
                    "stored": str(charge.total_paid),
                    "ledger": str(expected),
                }
            )
            if fix:
                charge.total_paid = expected
                charge.save(update_fields=["total_paid", "updated_at"])
                charge.refresh_from_db(fields=["balance"])
        recalculate_status(charge)

    return {"checked": qs.count(), "drift": drift, "fixed": fix and bool(drift)}


def ensure_charge_for_new_assignment(assignment, *, actor=None):
    """Bill a student who joins mid-month (TASK-063).

    Only fires once the month has actually been generated — otherwise the
    monthly run would be pre-empted for a single student. Idempotent through
    ``get_or_create``, so a transfer inside the same month reuses the existing
    charge instead of re-pricing it.
    """
    month = normalize_month(timezone.localdate())
    if not MonthlyCharge.objects.filter(billing_month=month).exists():
        return None
    charge, _created = create_charge_for_assignment(
        assignment, month, actor=actor, generated=GeneratedBy.MANUAL
    )
    return charge
