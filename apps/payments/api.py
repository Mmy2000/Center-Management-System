"""Payment endpoints (TASK-062 → 067)."""

from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.models import Group
from apps.accounts.decorators import require_feature, require_perm
from apps.core.http import DomainError, ajax
from apps.students.models import Student

from . import documents, services
from .models import ChargeStatus, MonthlyCharge, Payment, PaymentKind, PaymentMethod

PAGE_SIZE = 50
ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


def money(value) -> str:
    """Money always renders with two decimals — SUM() gives back Decimal('900')."""
    return str((value or ZERO).quantize(CENTS))


def charge_json(charge: MonthlyCharge) -> dict:
    return {
        "id": charge.pk,
        "student_id": charge.student_id,
        "student": charge.student.full_name,
        "student_code": charge.student.student_code,
        "subject": str(charge.grade_subject.subject),
        "grade": str(charge.grade_subject.grade),
        "group": charge.group.name if charge.group_id else None,
        "billing_month": charge.billing_month.isoformat(),
        "amount_due": str(charge.amount_due),
        "discount_amount": str(charge.discount_amount),
        "total_paid": str(charge.total_paid),
        "balance": str(charge.balance),
        "status": charge.status,
        "status_display": charge.get_status_display(),
        "due_date": charge.due_date.isoformat() if charge.due_date else None,
    }


def payment_json(payment: Payment) -> dict:
    return {
        "id": payment.pk,
        "receipt_number": payment.receipt_number,
        "charge_id": payment.monthly_charge_id,
        "student_id": payment.student_id,
        "student": payment.student.full_name,
        "kind": payment.kind,
        "kind_display": payment.get_kind_display(),
        "amount": str(payment.amount),
        "signed_amount": str(payment.signed_amount),
        "method": payment.method,
        "method_display": payment.get_method_display(),
        "paid_at": timezone.localtime(payment.paid_at).strftime("%Y-%m-%d %H:%M"),
        "collected_by": payment.collected_by.display_name,
        "reference": payment.reference,
        "notes": payment.notes,
        "reverses": payment.reverses_id,
    }


def _decimal(value, field="amount"):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise DomainError(
            "ERR_VALIDATION", _("قيمة غير صحيحة"), field_errors={field: [_("قيمة غير صحيحة")]}
        ) from exc


def charge_queryset(request):
    qs = MonthlyCharge.objects.with_related()
    params = request.GET
    if params.get("month"):
        qs = qs.filter(billing_month=services.normalize_month(params["month"]))
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("group"):
        qs = qs.filter(group_id=params["group"])
    if params.get("subject"):
        qs = qs.filter(grade_subject__subject_id=params["subject"])
    if params.get("grade"):
        qs = qs.filter(grade_subject__grade_id=params["grade"])
    if params.get("student"):
        qs = qs.filter(student_id=params["student"])
    if params.get("outstanding") == "1":
        qs = qs.outstanding()
    if params.get("q"):
        from apps.students.services import search_students

        student_ids = search_students(Student.objects.all(), params["q"]).values_list(
            "id", flat=True
        )
        qs = qs.filter(student_id__in=list(student_ids[:200]))
    return qs


@ajax(methods=["GET"], perm="payments.view_monthlycharge", feature="payments")
def charges(request):
    qs = charge_queryset(request)
    paginator = Paginator(qs, PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page") or 1)

    totals = qs.billable().aggregate(
        expected=Sum("amount_due"),
        discount=Sum("discount_amount"),
        collected=Sum("total_paid"),
        outstanding=Sum("balance"),
        paid_count=Count("id", filter=Q(status=ChargeStatus.PAID)),
        partial_count=Count("id", filter=Q(status=ChargeStatus.PARTIALLY_PAID)),
        unpaid_count=Count("id", filter=Q(status=ChargeStatus.UNPAID)),
    )
    return {
        "results": [charge_json(charge) for charge in page.object_list],
        "page": page.number,
        "pages": paginator.num_pages,
        "count": paginator.count,
        "has_next": page.has_next(),
        "has_previous": page.has_previous(),
        "totals": {
            "expected": money(totals["expected"]),
            "discount": money(totals["discount"]),
            "collected": money(totals["collected"]),
            "outstanding": money(totals["outstanding"]),
            "paid_count": totals["paid_count"],
            "partial_count": totals["partial_count"],
            "unpaid_count": totals["unpaid_count"],
        },
    }


@ajax(methods=["GET", "PATCH"], perm="payments.view_monthlycharge", feature="payments")
def charge_detail(request, pk):
    charge = get_object_or_404(MonthlyCharge.objects.with_related(), pk=pk)
    if request.method == "GET":
        return {
            "charge": charge_json(charge),
            "payments": [
                payment_json(p)
                for p in charge.payments.select_related("student", "collected_by").order_by("paid_at")
            ],
        }

    if not request.user.has_perm("payments.change_monthlycharge"):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

    data = request.json
    charge = services.update_charge(
        charge,
        actor=request.user,
        reason=data.get("reason", ""),
        amount_due=data.get("amount_due"),
        discount_amount=data.get("discount_amount"),
        due_date=(
            datetime.strptime(data["due_date"], "%Y-%m-%d").date()
            if data.get("due_date")
            else None
        ),
    )
    return {"charge": charge_json(charge)}


@ajax(methods=["POST"], perm="payments.generate_charges", feature="payments")
def generate(request):
    data = request.json
    group = None
    if data.get("group_id"):
        group = get_object_or_404(Group, pk=data["group_id"])

    summary = services.generate_monthly_charges(
        data.get("billing_month") or timezone.localdate(),
        actor=request.user,
        group=group,
        dry_run=bool(data.get("dry_run")),
    )
    return summary


@ajax(methods=["POST"], perm="payments.waive_charge", feature="payments.waivers")
def waive(request, pk):
    charge = get_object_or_404(MonthlyCharge.objects.with_related(), pk=pk)
    charge = services.waive_charge(charge, actor=request.user, reason=request.json.get("reason", ""))
    return {"charge": charge_json(charge)}


@ajax(methods=["POST"], perm="payments.change_monthlycharge", feature="payments")
def cancel(request, pk):
    charge = get_object_or_404(MonthlyCharge.objects.with_related(), pk=pk)
    charge = services.cancel_charge(charge, actor=request.user, reason=request.json.get("reason", ""))
    return {"charge": charge_json(charge)}


@ajax(methods=["GET", "POST"], perm="payments.view_payment", feature="payments")
def payments(request):
    if request.method == "GET":
        qs = Payment.objects.select_related("student", "collected_by", "monthly_charge")
        params = request.GET
        if params.get("from"):
            qs = qs.filter(paid_at__date__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(paid_at__date__lte=params["to"])
        if params.get("method"):
            qs = qs.filter(method=params["method"])
        if params.get("cashier"):
            qs = qs.filter(collected_by_id=params["cashier"])
        if params.get("student"):
            qs = qs.filter(student_id=params["student"])

        paginator = Paginator(qs, PAGE_SIZE)
        page = paginator.get_page(params.get("page") or 1)
        totals = qs.aggregate(
            collected=Sum("amount", filter=Q(kind=PaymentKind.PAYMENT)),
            refunded=Sum("amount", filter=Q(kind=PaymentKind.REFUND)),
            count=Count("id"),
        )
        net = (totals["collected"] or ZERO) - (totals["refunded"] or ZERO)
        by_method = list(
            qs.values("method").annotate(
                total=Sum("amount", filter=Q(kind=PaymentKind.PAYMENT)), count=Count("id")
            )
        )
        return {
            "results": [payment_json(p) for p in page.object_list],
            "page": page.number,
            "pages": paginator.num_pages,
            "count": paginator.count,
            "has_next": page.has_next(),
            "has_previous": page.has_previous(),
            "totals": {
                "collected": money(totals["collected"]),
                "refunded": money(totals["refunded"]),
                "net": money(net),
                "count": totals["count"],
            },
            "by_method": [
                {"method": row["method"], "total": money(row["total"]), "count": row["count"]}
                for row in by_method
            ],
        }

    if not request.user.has_perm("payments.add_payment"):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

    data = request.json
    charge = get_object_or_404(MonthlyCharge.objects.with_related(), pk=data.get("charge_id"))
    paid_at = None
    if data.get("paid_at"):
        paid_at = datetime.fromisoformat(data["paid_at"])
        if timezone.is_naive(paid_at):
            paid_at = timezone.make_aware(paid_at, timezone.get_current_timezone())

    payment = services.record_payment(
        charge,
        _decimal(data.get("amount")),
        collected_by=request.user,
        method=data.get("method") or PaymentMethod.CASH,
        paid_at=paid_at,
        reference=data.get("reference", ""),
        notes=data.get("notes", ""),
    )
    charge.refresh_from_db()
    return {"payment": payment_json(payment), "charge": charge_json(charge)}


@ajax(methods=["POST"], perm="payments.refund_payment", feature="payments.refunds")
def refund(request, pk):
    payment = get_object_or_404(Payment.objects.select_related("monthly_charge"), pk=pk)
    data = request.json
    refund_row = services.refund_payment(
        payment,
        _decimal(data["amount"]) if data.get("amount") else None,
        actor=request.user,
        reason=data.get("reason", ""),
    )
    charge = MonthlyCharge.objects.with_related().get(pk=payment.monthly_charge_id)
    return {"payment": payment_json(refund_row), "charge": charge_json(charge)}


@ajax(methods=["GET"], perm="payments.view_payment", feature="payments")
def student_payments(request, pk):
    student = get_object_or_404(Student, pk=pk)
    rows = student.payments.select_related("collected_by", "monthly_charge__grade_subject__subject")
    return {"results": [payment_json(p) for p in rows]}


@ajax(methods=["GET"], perm="payments.view_monthlycharge", feature="payments")
def student_financial_summary(request, pk):
    """Per-month, per-subject picture for the student profile (§33)."""
    student = get_object_or_404(Student, pk=pk)
    month = request.GET.get("month")
    qs = MonthlyCharge.objects.with_related().filter(student=student)
    if month:
        qs = qs.filter(billing_month=services.normalize_month(month))

    totals = qs.billable().aggregate(
        due=Sum("amount_due"), discount=Sum("discount_amount"),
        paid=Sum("total_paid"), balance=Sum("balance"),
    )
    return {
        "results": [charge_json(charge) for charge in qs],
        "totals": {key: money(value) for key, value in totals.items()},
    }


@require_perm("payments.view_payment")
@require_feature("payments.receipt_pdf")
def receipt(request, pk):
    """Downloadable PDF receipt — A5 by default, ``?size=thermal`` for a roll."""
    payment = get_object_or_404(
        Payment.objects.select_related(
            "student", "collected_by", "monthly_charge__grade_subject__subject",
            "monthly_charge__grade_subject__grade",
        ),
        pk=pk,
    )
    size = documents.THERMAL if request.GET.get("size") == "thermal" else documents.A5
    document = documents.build_receipt(payment, request=request, size=size)
    return document.response(documents.receipt_filename(payment))
