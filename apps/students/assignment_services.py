"""Group assignment services (TASK-036, TASK-037).

Principle 2 in code: these are the *only* functions in the system allowed to
write StudentGroupAssignment. The attendance app never calls them.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record
from apps.core.http import DomainError
from apps.core.models import AuditAction

from .models import AssignmentStatus, EndReason, StudentGroupAssignment


def active_assignment(student, grade_subject_id):
    """The student's usual group for one offering — the scan hot path."""
    return (
        StudentGroupAssignment.objects.filter(
            student=student, grade_subject_id=grade_subject_id, status=AssignmentStatus.ACTIVE
        )
        .select_related("group")
        .first()
    )


def group_is_full(group) -> bool:
    if not group.capacity:
        return False
    current = StudentGroupAssignment.objects.filter(
        group=group, status=AssignmentStatus.ACTIVE
    ).count()
    return current >= group.capacity


@transaction.atomic
def assign_student(
    student,
    group,
    *,
    actor=None,
    start_date=None,
    is_default=True,
    notes="",
    allow_grade_mismatch=False,
):
    """Enrol a student in a group. Capacity is advisory; grade is not."""
    if group.grade_subject.grade_id != student.grade_id and not allow_grade_mismatch:
        raise DomainError(
            "ERR_GRADE_MISMATCH",
            _("صف الطالب لا يطابق صف المجموعة"),
            status=409,
            field_errors={"group": [_("صف المجموعة مختلف عن صف الطالب")]},
        )

    warnings = []
    if group_is_full(group):
        warnings.append("WARN_CAPACITY")

    try:
        assignment = StudentGroupAssignment.objects.create(
            student=student,
            group=group,
            grade_subject=group.grade_subject,
            is_default=is_default,
            start_date=start_date or timezone.localdate(),
            notes=notes,
            created_by=actor,
        )
    except IntegrityError as exc:
        raise DomainError(
            "ERR_ALREADY_ASSIGNED",
            _("الطالب مشترك بالفعل في مجموعة أخرى لنفس المادة — استخدم النقل"),
            status=409,
        ) from exc

    record(
        AuditAction.STUDENT_ASSIGNED,
        assignment,
        changes={
            "student": str(student),
            "group": str(group),
            "subject": str(group.grade_subject.subject),
        },
        reason=notes,
        actor=actor,
    )

    # No student should be silently unbilled (TASK-063). Imported here to keep
    # the dependency one-way: payments knows nothing about assignments.
    from apps.payments.services import ensure_charge_for_new_assignment

    ensure_charge_for_new_assignment(assignment, actor=actor)
    return assignment, warnings


@transaction.atomic
def end_assignment(
    assignment,
    *,
    actor=None,
    end_date=None,
    reason=EndReason.LEFT_CENTER,
    status=AssignmentStatus.ENDED,
    notes="",
):
    """Close an assignment. The row is never deleted (Principle 5)."""
    if assignment.status != AssignmentStatus.ACTIVE:
        raise DomainError("ERR_ASSIGNMENT_NOT_ACTIVE", _("الاشتراك غير نشط"), status=409)

    assignment.end_date = end_date or timezone.localdate()
    assignment.status = status
    assignment.end_reason = reason
    if notes:
        assignment.notes = f"{assignment.notes}\n{notes}".strip()
    assignment.save(update_fields=["end_date", "status", "end_reason", "notes", "updated_at"])

    record(
        AuditAction.STUDENT_UNASSIGNED,
        assignment,
        changes={"status": {"old": AssignmentStatus.ACTIVE, "new": status}},
        reason=notes or reason,
        actor=actor,
    )
    return assignment


@transaction.atomic
def transfer_student(assignment, new_group, *, actor=None, start_date=None, reason=""):
    """Move a student between groups of the *same* offering, keeping history.

    Two rows result: the old one ENDED (still pointing at the old group) and a
    new ACTIVE one. Monthly charges are untouched — the charge is keyed on the
    offering, not the group.
    """
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب النقل مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    if assignment.group_id == new_group.pk:
        raise DomainError("ERR_SAME_GROUP", _("الطالب بالفعل في هذه المجموعة"), status=409)
    if new_group.grade_subject_id != assignment.grade_subject_id:
        raise DomainError(
            "ERR_DIFFERENT_OFFERING",
            _("النقل مسموح فقط بين مجموعات نفس المادة والصف"),
            status=409,
        )

    old_group = assignment.group
    today = start_date or timezone.localdate()
    end_assignment(
        assignment,
        actor=actor,
        end_date=today,
        reason=EndReason.GROUP_CHANGE,
        status=AssignmentStatus.TRANSFERRED,
    )
    new_assignment, warnings = assign_student(
        assignment.student,
        new_group,
        actor=actor,
        start_date=today,
        is_default=assignment.is_default,
        notes=reason,
    )

    record(
        AuditAction.STUDENT_TRANSFERRED,
        new_assignment,
        changes={"group": {"old": str(old_group), "new": str(new_group)}},
        reason=reason,
        actor=actor,
    )
    return new_assignment, warnings
