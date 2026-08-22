"""Report queries (TASK-072 → 074).

Every total is computed by the database (``Sum``/``Count`` with ``filter=``);
nothing loops over a queryset in Python to add numbers up. Each function takes
the parsed filter dict and returns a list of plain dicts.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.attendance.models import Attendance, AttendanceState, AttendanceStatus, AttendanceType
from apps.lessons.models import Lesson, LessonStatus
from apps.payments.models import ChargeStatus, MonthlyCharge, Payment, PaymentKind
from apps.students.models import AssignmentStatus, StudentGroupAssignment

ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=12, decimal_places=2)

PRESENT_STATES = [AttendanceState.CHECKED_IN, AttendanceState.CHECKED_OUT]
IS_PRESENT = Q(state__in=PRESENT_STATES) & Q(status__in=[AttendanceStatus.PRESENT, AttendanceStatus.PARTIAL])
IS_LATE = Q(status=AttendanceStatus.LATE)
IS_ABSENT = Q(state=AttendanceState.ABSENT)
IS_ALTERNATIVE = ~Q(attendance_type=AttendanceType.NORMAL) & ~Q(state=AttendanceState.ABSENT)


def _rate(present, late, absent) -> str:
    attended = (present or 0) + (late or 0)
    total = attended + (absent or 0)
    return f"{(attended / total * 100):.0f}" if total else "—"


def _money(value) -> str:
    return str((value or ZERO).quantize(Decimal("0.01")))


def _default_range(filters) -> tuple[date, date]:
    today = timezone.localdate()
    start = filters.get("from") or today.replace(day=1)
    end = filters.get("to") or today
    return start, end


def _attendance_qs(filters):
    start, end = _default_range(filters)
    qs = Attendance.objects.filter(
        lesson__lesson_date__gte=start, lesson__lesson_date__lte=end
    ).exclude(state=AttendanceState.CANCELLED)

    if filters.get("group"):
        qs = qs.filter(attended_group_id=filters["group"])
    if filters.get("subject"):
        qs = qs.filter(lesson__group__grade_subject__subject_id=filters["subject"])
    if filters.get("grade"):
        qs = qs.filter(lesson__group__grade_subject__grade_id=filters["grade"])
    if filters.get("stage"):
        qs = qs.filter(lesson__group__grade_subject__grade__stage_id=filters["stage"])
    if filters.get("instructor"):
        qs = qs.filter(lesson__group__instructor_id=filters["instructor"])
    if filters.get("student"):
        qs = qs.filter(student_id=filters["student"])
    return qs


def _lesson_qs(filters):
    start, end = _default_range(filters)
    qs = Lesson.objects.filter(lesson_date__gte=start, lesson_date__lte=end).exclude(
        status=LessonStatus.CANCELLED
    )
    if filters.get("group"):
        qs = qs.filter(group_id=filters["group"])
    if filters.get("subject"):
        qs = qs.filter(group__grade_subject__subject_id=filters["subject"])
    if filters.get("grade"):
        qs = qs.filter(group__grade_subject__grade_id=filters["grade"])
    if filters.get("stage"):
        qs = qs.filter(group__grade_subject__grade__stage_id=filters["stage"])
    if filters.get("instructor"):
        qs = qs.filter(group__instructor_id=filters["instructor"])
    return qs


# Counting attendances *from a Lesson* needs the relation prefix: a bare
# Q(state=...) would try to resolve "state" on Lesson itself.
LESSON_PRESENT = Q(
    attendances__state__in=PRESENT_STATES,
    attendances__status__in=[AttendanceStatus.PRESENT, AttendanceStatus.PARTIAL],
)
LESSON_LATE = Q(attendances__status=AttendanceStatus.LATE)
LESSON_ABSENT = Q(attendances__state=AttendanceState.ABSENT)
LESSON_ALTERNATIVE = ~Q(attendances__attendance_type=AttendanceType.NORMAL) & ~Q(
    attendances__state=AttendanceState.ABSENT
)

ATTENDANCE_AGGREGATES = {
    "present": Count("id", filter=IS_PRESENT),
    "late": Count("id", filter=IS_LATE),
    "absent": Count("id", filter=IS_ABSENT),
    "alternative": Count("id", filter=IS_ALTERNATIVE),
}


# --------------------------------------------------------------------------- #
# Attendance
# --------------------------------------------------------------------------- #

def attendance_daily(filters):
    rows = (
        _attendance_qs(filters)
        .values(day=F("lesson__lesson_date"))
        .annotate(lessons=Count("lesson", distinct=True), **ATTENDANCE_AGGREGATES)
        .order_by("-day")
    )
    return [
        {
            "date": row["day"].isoformat(),
            "lessons": row["lessons"],
            "present": row["present"],
            "late": row["late"],
            "absent": row["absent"],
            "alternative": row["alternative"],
            "rate": _rate(row["present"], row["late"], row["absent"]),
        }
        for row in rows
    ]


def attendance_by_lesson(filters):
    rows = (
        _lesson_qs(filters)
        .select_related("group__grade_subject__subject", "group__instructor")
        .annotate(
            present=Count("attendances", filter=LESSON_PRESENT),
            late=Count("attendances", filter=LESSON_LATE),
            absent=Count("attendances", filter=LESSON_ABSENT),
            alternative=Count("attendances", filter=LESSON_ALTERNATIVE),
        )
        .order_by("-lesson_date", "-scheduled_start")
    )
    return [
        {
            "date": lesson.lesson_date.isoformat(),
            "subject": str(lesson.group.grade_subject.subject),
            "group": lesson.group.name,
            "instructor": str(lesson.group.instructor) if lesson.group.instructor_id else "—",
            "expected": lesson.expected_students,
            "present": lesson.present,
            "late": lesson.late,
            "absent": lesson.absent,
            "alternative": lesson.alternative,
        }
        for lesson in rows
    ]


def attendance_by_student(filters):
    rows = (
        _attendance_qs(filters)
        .values(
            "student_id",
            code=F("student__student_code"),
            name=F("student__full_name"),
            grade=F("student__grade__name"),
        )
        .annotate(**ATTENDANCE_AGGREGATES)
        .order_by("name")
    )
    return [
        {
            "student_code": row["code"],
            "student": row["name"],
            "grade": row["grade"],
            "present": row["present"],
            "late": row["late"],
            "absent": row["absent"],
            "alternative": row["alternative"],
            "rate": _rate(row["present"], row["late"], row["absent"]),
        }
        for row in rows
    ]


def attendance_by_group(filters):
    rows = (
        _attendance_qs(filters)
        .values(
            "attended_group_id",
            group=F("attended_group__name"),
            subject=F("attended_group__grade_subject__subject__name"),
            grade=F("attended_group__grade_subject__grade__name"),
        )
        .annotate(lessons=Count("lesson", distinct=True), **ATTENDANCE_AGGREGATES)
        .order_by("subject", "group")
    )
    roster = dict(
        StudentGroupAssignment.objects.filter(status=AssignmentStatus.ACTIVE)
        .values("group_id")
        .annotate(total=Count("id"))
        .values_list("group_id", "total")
    )
    return [
        {
            "group": row["group"],
            "subject": row["subject"],
            "grade": row["grade"],
            "students": roster.get(row["attended_group_id"], 0),
            "lessons": row["lessons"],
            "present": row["present"],
            "late": row["late"],
            "absent": row["absent"],
            "rate": _rate(row["present"], row["late"], row["absent"]),
        }
        for row in rows
    ]


def attendance_by_subject(filters):
    rows = (
        _attendance_qs(filters)
        .values(
            subject=F("lesson__group__grade_subject__subject__name"),
            grade=F("lesson__group__grade_subject__grade__name"),
        )
        .annotate(lessons=Count("lesson", distinct=True), **ATTENDANCE_AGGREGATES)
        .order_by("subject", "grade")
    )
    return [
        {
            "subject": row["subject"],
            "grade": row["grade"],
            "lessons": row["lessons"],
            "present": row["present"],
            "late": row["late"],
            "absent": row["absent"],
            "rate": _rate(row["present"], row["late"], row["absent"]),
        }
        for row in rows
    ]


def late_students(filters):
    rows = (
        _attendance_qs(filters)
        .filter(status=AttendanceStatus.LATE)
        .select_related("student", "attended_group__grade_subject__subject", "lesson")
        .order_by("-lesson__lesson_date", "-late_minutes")
    )
    return [
        {
            "date": a.lesson.lesson_date.isoformat(),
            "student_code": a.student.student_code,
            "student": a.student.full_name,
            "subject": str(a.attended_group.grade_subject.subject),
            "group": a.attended_group.name,
            "check_in": timezone.localtime(a.check_in_at).strftime("%H:%M") if a.check_in_at else "—",
            "late_minutes": a.late_minutes,
        }
        for a in rows
    ]


def absent_students(filters):
    rows = (
        _attendance_qs(filters)
        .filter(state=AttendanceState.ABSENT)
        .select_related("student", "attended_group__grade_subject__subject", "lesson")
        .order_by("-lesson__lesson_date", "student__full_name")
    )
    return [
        {
            "date": a.lesson.lesson_date.isoformat(),
            "student_code": a.student.student_code,
            "student": a.student.full_name,
            "subject": str(a.attended_group.grade_subject.subject),
            "group": a.attended_group.name,
            "guardian_phone": a.student.guardian_phone or "—",
        }
        for a in rows
    ]


def alternative_groups(filters):
    """§35 — who sat in a group that is not their own, and how often."""
    qs = (
        _attendance_qs(filters)
        .exclude(attendance_type=AttendanceType.NORMAL)
        .exclude(state=AttendanceState.ABSENT)
        .select_related("student", "assigned_group", "attended_group__grade_subject__subject", "lesson")
    )
    if filters.get("type"):
        qs = qs.filter(attendance_type=filters["type"])

    rows = list(qs.order_by("-lesson__lesson_date", "student__full_name"))

    frequency: dict[tuple, int] = {}
    for a in rows:
        key = (a.student_id, a.lesson.lesson_date.replace(day=1))
        frequency[key] = frequency.get(key, 0) + 1

    return [
        {
            "date": a.lesson.lesson_date.isoformat(),
            "student_code": a.student.student_code,
            "student": a.student.full_name,
            "subject": str(a.attended_group.grade_subject.subject),
            "assigned_group": a.assigned_group.name if a.assigned_group_id else "—",
            "attended_group": a.attended_group.name,
            "type": a.get_attendance_type_display(),
            "times_this_month": frequency[(a.student_id, a.lesson.lesson_date.replace(day=1))],
        }
        for a in rows
    ]


# --------------------------------------------------------------------------- #
# Finance
# --------------------------------------------------------------------------- #

def _payment_qs(filters):
    start, end = _default_range(filters)
    qs = Payment.objects.filter(paid_at__date__gte=start, paid_at__date__lte=end)
    if filters.get("method"):
        qs = qs.filter(method=filters["method"])
    if filters.get("cashier"):
        qs = qs.filter(collected_by_id=filters["cashier"])
    if filters.get("student"):
        qs = qs.filter(student_id=filters["student"])
    return qs


COLLECTED = Coalesce(Sum("amount", filter=Q(kind=PaymentKind.PAYMENT)), Value(ZERO), output_field=MONEY)
REFUNDED = Coalesce(Sum("amount", filter=Q(kind=PaymentKind.REFUND)), Value(ZERO), output_field=MONEY)


def collection_daily(filters):
    rows = (
        _payment_qs(filters)
        .values(day=F("paid_at__date"))
        .annotate(count=Count("id"), collected=COLLECTED, refunded=REFUNDED)
        .order_by("-day")
    )
    return [
        {
            "date": row["day"].isoformat() if hasattr(row["day"], "isoformat") else str(row["day"]),
            "count": row["count"],
            "collected": _money(row["collected"]),
            "refunded": _money(row["refunded"]),
            "net": _money(row["collected"] - row["refunded"]),
        }
        for row in rows
    ]


def cashier_daybook(filters):
    rows = (
        _payment_qs(filters)
        .values(cashier=F("collected_by__username"), method_key=F("method"))
        .annotate(count=Count("id"), collected=COLLECTED, refunded=REFUNDED)
        .order_by("cashier", "method_key")
    )
    labels = dict(Payment._meta.get_field("method").choices)
    return [
        {
            "cashier": row["cashier"],
            "method": str(labels.get(row["method_key"], row["method_key"])),
            "count": row["count"],
            "collected": _money(row["collected"]),
            "refunded": _money(row["refunded"]),
            "net": _money(row["collected"] - row["refunded"]),
        }
        for row in rows
    ]


def student_payments(filters):
    rows = (
        _payment_qs(filters)
        .select_related("student", "collected_by", "monthly_charge__grade_subject__subject")
        .order_by("-paid_at")
    )
    return [
        {
            "receipt": p.receipt_number,
            "date": timezone.localtime(p.paid_at).strftime("%Y-%m-%d %H:%M"),
            "student_code": p.student.student_code,
            "student": p.student.full_name,
            "subject": str(p.monthly_charge.grade_subject.subject),
            "kind": p.get_kind_display(),
            "amount": _money(p.signed_amount),
            "method": p.get_method_display(),
            "cashier": p.collected_by.display_name,
        }
        for p in rows
    ]


def _charge_qs(filters):
    qs = MonthlyCharge.objects.billable()
    if filters.get("month"):
        qs = qs.filter(billing_month=filters["month"])
    if filters.get("group"):
        qs = qs.filter(group_id=filters["group"])
    if filters.get("subject"):
        qs = qs.filter(grade_subject__subject_id=filters["subject"])
    if filters.get("grade"):
        qs = qs.filter(grade_subject__grade_id=filters["grade"])
    if filters.get("stage"):
        qs = qs.filter(grade_subject__grade__stage_id=filters["stage"])
    if filters.get("student"):
        qs = qs.filter(student_id=filters["student"])
    return qs


def outstanding(filters):
    rows = (
        _charge_qs(filters)
        .filter(balance__gt=0)
        .select_related("student", "grade_subject__subject", "group")
        .order_by("-balance")
    )
    return [
        {
            "student_code": c.student.student_code,
            "student": c.student.full_name,
            "guardian_phone": c.student.guardian_phone or "—",
            "subject": str(c.grade_subject.subject),
            "group": c.group.name if c.group_id else "—",
            "month": c.billing_month.strftime("%Y-%m"),
            "amount_due": _money(c.amount_due),
            "total_paid": _money(c.total_paid),
            "balance": _money(c.balance),
            "status": c.get_status_display(),
        }
        for c in rows
    ]


def finance_by_group(filters):
    rows = (
        _charge_qs(filters)
        .values("group_id", group_name=F("group__name"), subject=F("grade_subject__subject__name"))
        .annotate(
            students=Count("student", distinct=True),
            expected=Coalesce(Sum(F("amount_due") - F("discount_amount")), Value(ZERO), output_field=MONEY),
            collected=Coalesce(Sum("total_paid"), Value(ZERO), output_field=MONEY),
            outstanding_amount=Coalesce(Sum("balance"), Value(ZERO), output_field=MONEY),
        )
        .order_by("subject", "group")
    )
    result = []
    for row in rows:
        expected = row["expected"] or ZERO
        collected = row["collected"] or ZERO
        result.append(
            {
                "group": row["group_name"] or "—",
                "subject": row["subject"],
                "students": row["students"],
                "expected": _money(expected),
                "collected": _money(collected),
                "outstanding": _money(row["outstanding_amount"]),
                "rate": f"{(collected / expected * 100):.0f}" if expected else "—",
            }
        )
    return result


def finance_by_subject(filters):
    rows = (
        _charge_qs(filters)
        .values(subject=F("grade_subject__subject__name"), grade=F("grade_subject__grade__name"))
        .annotate(
            charges=Count("id"),
            expected=Coalesce(Sum(F("amount_due") - F("discount_amount")), Value(ZERO), output_field=MONEY),
            collected=Coalesce(Sum("total_paid"), Value(ZERO), output_field=MONEY),
            outstanding_amount=Coalesce(Sum("balance"), Value(ZERO), output_field=MONEY),
        )
        .order_by("subject", "grade")
    )
    return [
        {
            "subject": row["subject"],
            "grade": row["grade"],
            "charges": row["charges"],
            "expected": _money(row["expected"]),
            "collected": _money(row["collected"]),
            "outstanding": _money(row["outstanding_amount"]),
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Dashboard (TASK-069)
# --------------------------------------------------------------------------- #

def dashboard_summary(user=None) -> dict:
    from apps.accounts.scoping import visible_lessons

    today = timezone.localdate()
    month = today.replace(day=1)

    lessons = Lesson.objects.filter(lesson_date=today)
    if user is not None:
        lessons = visible_lessons(user, lessons)

    lesson_stats = lessons.aggregate(
        total=Count("id"),
        active=Count("id", filter=Q(status=LessonStatus.OPEN)),
        completed=Count("id", filter=Q(status=LessonStatus.COMPLETED)),
        expected=Coalesce(Sum("expected_students"), Value(0)),
    )
    attendance_today = Attendance.objects.filter(lesson__lesson_date=today).aggregate(
        **ATTENDANCE_AGGREGATES
    )
    money = MonthlyCharge.objects.billable().filter(billing_month=month).aggregate(
        expected=Coalesce(Sum(F("amount_due") - F("discount_amount")), Value(ZERO), output_field=MONEY),
        collected=Coalesce(Sum("total_paid"), Value(ZERO), output_field=MONEY),
        outstanding=Coalesce(Sum("balance"), Value(ZERO), output_field=MONEY),
        paid=Count("id", filter=Q(status=ChargeStatus.PAID)),
        partial=Count("id", filter=Q(status=ChargeStatus.PARTIALLY_PAID)),
        unpaid=Count("id", filter=Q(status=ChargeStatus.UNPAID)),
    )
    collected_today = Payment.objects.filter(paid_at__date=today).aggregate(
        collected=COLLECTED, refunded=REFUNDED
    )

    open_lessons = [
        {
            "id": lesson.pk,
            "group": lesson.group.name,
            "subject": str(lesson.group.grade_subject.subject),
            "start": timezone.localtime(lesson.scheduled_start).strftime("%H:%M"),
            "expected": lesson.expected_students,
        }
        for lesson in lessons.filter(status=LessonStatus.OPEN)
        .select_related("group__grade_subject__subject")
        .order_by("scheduled_start")[:10]
    ]

    return {
        "today": {
            "date": today.isoformat(),
            "lessons": lesson_stats["total"],
            "active_lessons": lesson_stats["active"],
            "completed_lessons": lesson_stats["completed"],
            "expected_students": lesson_stats["expected"],
            "present": attendance_today["present"],
            "late": attendance_today["late"],
            "absent": attendance_today["absent"],
            "alternative": attendance_today["alternative"],
            "collected": _money(collected_today["collected"] - collected_today["refunded"]),
        },
        "month": {
            "month": month.strftime("%Y-%m"),
            "expected": _money(money["expected"]),
            "collected": _money(money["collected"]),
            "outstanding": _money(money["outstanding"]),
            "paid_students": money["paid"],
            "partial_students": money["partial"],
            "unpaid_students": money["unpaid"],
        },
        "open_lessons": open_lessons,
    }


def group_dashboard(group) -> dict:
    """§36 — roster, attendance and money for one group."""
    today = timezone.localdate()
    month = today.replace(day=1)

    roster = StudentGroupAssignment.objects.filter(
        group=group, status=AssignmentStatus.ACTIVE
    ).count()
    recent = Attendance.objects.filter(
        attended_group=group, lesson__lesson_date__gte=today - timedelta(days=30)
    ).aggregate(**ATTENDANCE_AGGREGATES)
    money = MonthlyCharge.objects.billable().filter(group=group, billing_month=month).aggregate(
        expected=Coalesce(Sum(F("amount_due") - F("discount_amount")), Value(ZERO), output_field=MONEY),
        collected=Coalesce(Sum("total_paid"), Value(ZERO), output_field=MONEY),
        outstanding=Coalesce(Sum("balance"), Value(ZERO), output_field=MONEY),
        paid=Count("id", filter=Q(status=ChargeStatus.PAID)),
        partial=Count("id", filter=Q(status=ChargeStatus.PARTIALLY_PAID)),
        unpaid=Count("id", filter=Q(status=ChargeStatus.UNPAID)),
    )
    last_lesson = (
        Lesson.objects.filter(group=group, lesson_date__lte=today)
        .exclude(status=LessonStatus.CANCELLED)
        .order_by("-scheduled_start")
        .first()
    )
    next_lesson = (
        Lesson.objects.filter(group=group, lesson_date__gte=today, status=LessonStatus.SCHEDULED)
        .order_by("scheduled_start")
        .first()
    )
    return {
        "students": roster,
        "capacity": group.capacity,
        "attendance_30d": {
            **recent,
            "rate": _rate(recent["present"], recent["late"], recent["absent"]),
        },
        "money": {
            "month": month.strftime("%Y-%m"),
            "expected": _money(money["expected"]),
            "collected": _money(money["collected"]),
            "outstanding": _money(money["outstanding"]),
            "paid": money["paid"],
            "partial": money["partial"],
            "unpaid": money["unpaid"],
        },
        "last_lesson": last_lesson.lesson_date.isoformat() if last_lesson else None,
        "next_lesson": next_lesson.lesson_date.isoformat() if next_lesson else None,
    }
