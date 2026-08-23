"""Report registry (TASK-071).

Adding a report means adding one entry here plus one query function in
``queries.py`` — the filter bar, the table, pagination, permissions and the
exporters are shared.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

ATTENDANCE = "attendance"
FINANCE = "finance"

VIEW_ATTENDANCE = "reports.view_reports"
VIEW_FINANCE = "reports.view_financial_reports"


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    money: bool = False
    align: str = "start"


#: Which feature each report group needs. A center that did not buy payments
#: must not see the finance reports at all — not the tab, not the slug, not the
#: export (docs/10 §N.8).
GROUP_FEATURES = {
    ATTENDANCE: "reports.operational",
    FINANCE: "reports.financial",
}


@dataclass(frozen=True)
class Report:
    slug: str
    title: str
    group: str
    permission: str
    query: Callable
    columns: list[Column]
    filters: tuple[str, ...] = ()
    description: str = ""
    totals: tuple[str, ...] = field(default_factory=tuple)


_REGISTRY: dict[str, Report] = {}


def register(report: Report) -> Report:
    if report.slug in _REGISTRY:
        raise ValueError(f"Duplicate report slug: {report.slug}")
    _REGISTRY[report.slug] = report
    return report


def get(slug: str) -> Report:
    try:
        return _REGISTRY[slug]
    except KeyError as exc:
        raise KeyError(f"Unknown report: {slug!r}") from exc


def all_reports() -> list[Report]:
    return list(_REGISTRY.values())


def feature_for(report: Report) -> str:
    """The feature key this report belongs to."""
    return GROUP_FEATURES.get(report.group, "reports.operational")


def is_available(report: Report) -> bool:
    """Whether the current center bought the feature this report belongs to."""
    from apps.tenancy.resolver import has_feature

    return has_feature(feature_for(report))


def for_user(user) -> list[Report]:
    """Reports this user may see, in this center.

    Permission and feature are both required and mean different things: one is
    about the person, the other about what the center bought.
    """
    return [r for r in _REGISTRY.values() if user.has_perm(r.permission) and is_available(r)]


def build():
    """Populate the registry. Called once at app ready."""
    from . import queries as q

    if _REGISTRY:
        return

    # ---------------------------------------------------------- attendance --
    register(
        Report(
            slug="attendance-daily",
            title=_("الحضور اليومي"),
            group=ATTENDANCE,
            permission=VIEW_ATTENDANCE,
            query=q.attendance_daily,
            filters=("from", "to", "stage", "grade", "subject", "group", "instructor"),
            columns=[
                Column("date", _("التاريخ")),
                Column("lessons", _("عدد الحصص")),
                Column("present", _("حاضر")),
                Column("late", _("متأخر")),
                Column("absent", _("غائب")),
                Column("alternative", _("مجموعة بديلة")),
                Column("rate", _("نسبة الحضور %")),
            ],
            totals=("lessons", "present", "late", "absent", "alternative"),
        )
    )
    register(
        Report(
            slug="attendance-lesson",
            title=_("حضور الحصص"),
            group=ATTENDANCE,
            permission=VIEW_ATTENDANCE,
            query=q.attendance_by_lesson,
            filters=("from", "to", "stage", "grade", "subject", "group", "instructor"),
            columns=[
                Column("date", _("التاريخ")),
                Column("subject", _("المادة")),
                Column("group", _("المجموعة")),
                Column("instructor", _("المدرّس")),
                Column("expected", _("متوقع")),
                Column("present", _("حاضر")),
                Column("late", _("متأخر")),
                Column("absent", _("غائب")),
                Column("alternative", _("بديل")),
            ],
            totals=("expected", "present", "late", "absent", "alternative"),
        )
    )
    register(
        Report(
            slug="attendance-student",
            title=_("حضور الطلاب"),
            group=ATTENDANCE,
            permission=VIEW_ATTENDANCE,
            query=q.attendance_by_student,
            filters=("from", "to", "stage", "grade", "subject", "group", "student"),
            columns=[
                Column("student_code", _("الكود")),
                Column("student", _("الطالب")),
                Column("grade", _("الصف")),
                Column("present", _("حاضر")),
                Column("late", _("متأخر")),
                Column("absent", _("غائب")),
                Column("alternative", _("بديل")),
                Column("rate", _("نسبة الحضور %")),
            ],
            totals=("present", "late", "absent", "alternative"),
        )
    )
    register(
        Report(
            slug="attendance-group",
            title=_("حضور المجموعات"),
            group=ATTENDANCE,
            permission=VIEW_ATTENDANCE,
            query=q.attendance_by_group,
            filters=("from", "to", "stage", "grade", "subject", "instructor"),
            columns=[
                Column("group", _("المجموعة")),
                Column("subject", _("المادة")),
                Column("grade", _("الصف")),
                Column("students", _("عدد الطلاب")),
                Column("lessons", _("حصص")),
                Column("present", _("حاضر")),
                Column("late", _("متأخر")),
                Column("absent", _("غائب")),
                Column("rate", _("نسبة الحضور %")),
            ],
            totals=("students", "lessons", "present", "late", "absent"),
        )
    )
    register(
        Report(
            slug="attendance-subject",
            title=_("حضور المواد"),
            group=ATTENDANCE,
            permission=VIEW_ATTENDANCE,
            query=q.attendance_by_subject,
            filters=("from", "to", "stage", "grade"),
            columns=[
                Column("subject", _("المادة")),
                Column("grade", _("الصف")),
                Column("lessons", _("حصص")),
                Column("present", _("حاضر")),
                Column("late", _("متأخر")),
                Column("absent", _("غائب")),
                Column("rate", _("نسبة الحضور %")),
            ],
            totals=("lessons", "present", "late", "absent"),
        )
    )
    register(
        Report(
            slug="late-students",
            title=_("الطلاب المتأخرون"),
            group=ATTENDANCE,
            permission=VIEW_ATTENDANCE,
            query=q.late_students,
            filters=("from", "to", "stage", "grade", "subject", "group"),
            columns=[
                Column("date", _("التاريخ")),
                Column("student_code", _("الكود")),
                Column("student", _("الطالب")),
                Column("subject", _("المادة")),
                Column("group", _("المجموعة")),
                Column("check_in", _("وقت الحضور")),
                Column("late_minutes", _("دقائق التأخير")),
            ],
            totals=("late_minutes",),
        )
    )
    register(
        Report(
            slug="absent-students",
            title=_("الطلاب الغائبون"),
            group=ATTENDANCE,
            permission=VIEW_ATTENDANCE,
            query=q.absent_students,
            filters=("from", "to", "stage", "grade", "subject", "group"),
            columns=[
                Column("date", _("التاريخ")),
                Column("student_code", _("الكود")),
                Column("student", _("الطالب")),
                Column("subject", _("المادة")),
                Column("group", _("المجموعة")),
                Column("guardian_phone", _("هاتف ولي الأمر")),
            ],
        )
    )
    register(
        Report(
            slug="alternative-groups",
            title=_("الحضور في مجموعات بديلة"),
            group=ATTENDANCE,
            permission=VIEW_ATTENDANCE,
            query=q.alternative_groups,
            filters=("from", "to", "stage", "grade", "subject", "group", "type"),
            description=_(
                "يوضّح الطلاب الذين حضروا في غير مجموعتهم المعتادة — للكشف عن "
                "الحاجة لنقل دائم أو ضغط على المواعيد."
            ),
            columns=[
                Column("date", _("التاريخ")),
                Column("student_code", _("الكود")),
                Column("student", _("الطالب")),
                Column("subject", _("المادة")),
                Column("assigned_group", _("مجموعته المعتادة")),
                Column("attended_group", _("المجموعة الحاضرة")),
                Column("type", _("النوع")),
                Column("times_this_month", _("مرات هذا الشهر")),
            ],
        )
    )

    # ------------------------------------------------------------- finance --
    register(
        Report(
            slug="collection-daily",
            title=_("التحصيل اليومي"),
            group=FINANCE,
            permission=VIEW_FINANCE,
            query=q.collection_daily,
            filters=("from", "to", "method", "cashier"),
            columns=[
                Column("date", _("التاريخ")),
                Column("count", _("عدد الحركات")),
                Column("collected", _("محصّل"), money=True),
                Column("refunded", _("مسترد"), money=True),
                Column("net", _("الصافي"), money=True),
            ],
            totals=("count", "collected", "refunded", "net"),
        )
    )
    register(
        Report(
            slug="outstanding",
            title=_("المتأخرات"),
            group=FINANCE,
            permission=VIEW_FINANCE,
            query=q.outstanding,
            filters=("month", "stage", "grade", "subject", "group"),
            columns=[
                Column("student_code", _("الكود")),
                Column("student", _("الطالب")),
                Column("guardian_phone", _("هاتف ولي الأمر")),
                Column("subject", _("المادة")),
                Column("group", _("المجموعة")),
                Column("month", _("الشهر")),
                Column("amount_due", _("المستحق"), money=True),
                Column("total_paid", _("المدفوع"), money=True),
                Column("balance", _("المتبقي"), money=True),
                Column("status", _("الحالة")),
            ],
            totals=("amount_due", "total_paid", "balance"),
        )
    )
    register(
        Report(
            slug="finance-group",
            title=_("الملخص المالي للمجموعات"),
            group=FINANCE,
            permission=VIEW_FINANCE,
            query=q.finance_by_group,
            filters=("month", "stage", "grade", "subject"),
            columns=[
                Column("group", _("المجموعة")),
                Column("subject", _("المادة")),
                Column("students", _("عدد الطلاب")),
                Column("expected", _("المتوقع"), money=True),
                Column("collected", _("المحصّل"), money=True),
                Column("outstanding", _("المتبقي"), money=True),
                Column("rate", _("نسبة التحصيل %")),
            ],
            totals=("students", "expected", "collected", "outstanding"),
        )
    )
    register(
        Report(
            slug="finance-subject",
            title=_("الملخص المالي للمواد"),
            group=FINANCE,
            permission=VIEW_FINANCE,
            query=q.finance_by_subject,
            filters=("month", "stage", "grade"),
            columns=[
                Column("subject", _("المادة")),
                Column("grade", _("الصف")),
                Column("charges", _("عدد الرسوم")),
                Column("expected", _("المتوقع"), money=True),
                Column("collected", _("المحصّل"), money=True),
                Column("outstanding", _("المتبقي"), money=True),
            ],
            totals=("charges", "expected", "collected", "outstanding"),
        )
    )
    register(
        Report(
            slug="cashier-daybook",
            title=_("يومية أمناء الخزنة"),
            group=FINANCE,
            permission=VIEW_FINANCE,
            query=q.cashier_daybook,
            filters=("from", "to", "cashier"),
            columns=[
                Column("cashier", _("المحصّل")),
                Column("method", _("الطريقة")),
                Column("count", _("عدد الحركات")),
                Column("collected", _("محصّل"), money=True),
                Column("refunded", _("مسترد"), money=True),
                Column("net", _("الصافي"), money=True),
            ],
            totals=("count", "collected", "refunded", "net"),
        )
    )
    register(
        Report(
            slug="student-payments",
            title=_("سجل مدفوعات الطلاب"),
            group=FINANCE,
            permission=VIEW_FINANCE,
            query=q.student_payments,
            filters=("from", "to", "student", "method"),
            columns=[
                Column("receipt", _("الإيصال")),
                Column("date", _("التاريخ")),
                Column("student_code", _("الكود")),
                Column("student", _("الطالب")),
                Column("subject", _("المادة")),
                Column("kind", _("النوع")),
                Column("amount", _("المبلغ"), money=True),
                Column("method", _("الطريقة")),
                Column("cashier", _("المحصّل")),
            ],
            totals=("amount",),
        )
    )
