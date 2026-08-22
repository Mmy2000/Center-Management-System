"""Eligibility classification — decision table 03 §D.4 as a pure function.

No database writes, and exactly one query (the active-assignment lookup, which
the caller may also pass in). This is the piece that decides NORMAL vs
ALTERNATIVE_GROUP vs MAKEUP vs EXCEPTIONAL — and it never touches the
assignment it reads (Principle 2).
"""

from dataclasses import dataclass

from apps.core.policies import ALLOW, BLOCK, REQUIRE_APPROVAL, WARN
from apps.core.registry import settings_registry

from .models import AttendanceType

# decision values
DECISION_ALLOW = ALLOW
DECISION_WARN = WARN
DECISION_APPROVAL = REQUIRE_APPROVAL
DECISION_BLOCK = BLOCK

SCENARIO_NORMAL = "A_NORMAL"
SCENARIO_ALTERNATIVE = "B_ALTERNATIVE"
SCENARIO_NOT_ASSIGNED = "C_NOT_ASSIGNED"
SCENARIO_OTHER_GRADE = "D_OTHER_GRADE"


@dataclass(frozen=True)
class Eligibility:
    scenario: str
    attendance_type: str
    decision: str
    assigned_group_id: int | None
    code: str
    block_code: str = ""


def resolve_assigned_group(student_id: int, grade_subject_id: int) -> tuple[int | None, str | None]:
    """The student's usual group for this offering — one indexed row.

    Returns ``(group_id, group_name)``: the name comes along for free, which
    keeps the scan response from costing an extra query just to render it.
    """
    from apps.students.models import AssignmentStatus, StudentGroupAssignment

    row = (
        StudentGroupAssignment.objects.filter(
            student_id=student_id,
            grade_subject_id=grade_subject_id,
            status=AssignmentStatus.ACTIVE,
        )
        .values_list("group_id", "group__name")
        .first()
    )
    return row if row else (None, None)


def classify(
    *,
    student,
    lesson_snapshot: dict,
    assigned_group_id: int | None,
    makeup_for_lesson_id: int | None = None,
) -> Eligibility:
    lesson_group_id = lesson_snapshot["group_id"]

    # A — the student's usual group.
    if assigned_group_id == lesson_group_id:
        return Eligibility(
            scenario=SCENARIO_NORMAL,
            attendance_type=AttendanceType.NORMAL,
            decision=DECISION_ALLOW,
            assigned_group_id=assigned_group_id,
            code="OK_CHECK_IN",
        )

    # B — assigned to this subject, sitting in another of its groups.
    if assigned_group_id is not None:
        policy = settings_registry.get("attendance.alternative_group_policy")
        is_makeup = makeup_for_lesson_id is not None
        return Eligibility(
            scenario=SCENARIO_ALTERNATIVE,
            attendance_type=AttendanceType.MAKEUP if is_makeup else AttendanceType.ALTERNATIVE_GROUP,
            decision=policy,
            assigned_group_id=assigned_group_id,
            code="OK_CHECK_IN_MAKEUP" if is_makeup else "OK_CHECK_IN_ALTERNATIVE",
            block_code="ERR_NOT_ASSIGNED_BLOCKED",
        )

    # D — a completely different grade (or subject) is the exceptional case.
    if student.grade_id != lesson_snapshot["grade_id"]:
        policy = settings_registry.get("attendance.different_grade_policy")
        return Eligibility(
            scenario=SCENARIO_OTHER_GRADE,
            attendance_type=AttendanceType.EXCEPTIONAL,
            decision=policy,
            assigned_group_id=None,
            code="NEEDS_APPROVAL_GRADE",
            block_code="ERR_GRADE_BLOCKED",
        )

    # C — same grade, but not enrolled in this subject.
    policy = settings_registry.get("attendance.not_assigned_policy")
    return Eligibility(
        scenario=SCENARIO_NOT_ASSIGNED,
        attendance_type=AttendanceType.EXCEPTIONAL,
        decision=policy,
        assigned_group_id=None,
        code="NEEDS_APPROVAL_NOT_ASSIGNED" if policy == REQUIRE_APPROVAL else "WARN_NOT_ASSIGNED",
        block_code="ERR_NOT_ASSIGNED_BLOCKED",
    )


def suggest_makeup(student_id: int, grade_subject_id: int, lesson_snapshot: dict) -> int | None:
    """Was the student absent from their own group this billing month?

    Used to offer a one-tap "record as make-up" on the scanner. Advisory only.
    """
    from django.utils import timezone

    from .models import Attendance, AttendanceState

    start = timezone.localtime(lesson_snapshot["scheduled_start"]).date().replace(day=1)
    return (
        Attendance.objects.filter(
            student_id=student_id,
            lesson__group__grade_subject_id=grade_subject_id,
            lesson__lesson_date__gte=start,
            state=AttendanceState.ABSENT,
        )
        .order_by("-lesson__lesson_date")
        .values_list("lesson_id", flat=True)
        .first()
    )
