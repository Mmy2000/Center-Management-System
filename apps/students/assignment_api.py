"""Assignment endpoints (TASK-036 → 038)."""

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from apps.academics.models import Group
from apps.accounts.scoping import visible_groups
from apps.core.http import DomainError, ajax

from . import assignment_services as svc
from .models import AssignmentStatus, Student, StudentGroupAssignment


def assignment_json(assignment) -> dict:
    return {
        "id": assignment.pk,
        "student_id": assignment.student_id,
        "student": assignment.student.full_name,
        "student_code": assignment.student.student_code,
        "group_id": assignment.group_id,
        "group": assignment.group.name,
        "group_code": assignment.group.code,
        "subject": str(assignment.grade_subject.subject),
        "grade": str(assignment.grade_subject.grade),
        "monthly_fee": str(assignment.group.monthly_fee),
        "is_default": assignment.is_default,
        "start_date": assignment.start_date.isoformat(),
        "end_date": assignment.end_date.isoformat() if assignment.end_date else None,
        "status": assignment.status,
        "status_display": assignment.get_status_display(),
        "end_reason": assignment.get_end_reason_display() if assignment.end_reason else "",
    }


def _assignments_qs():
    return StudentGroupAssignment.objects.select_related(
        "student", "group", "grade_subject__subject", "grade_subject__grade"
    )


@ajax(methods=["GET"], perm="students.view_studentgroupassignment")
def student_assignments(request, pk):
    """Active subscriptions plus the full history for one student."""
    student = get_object_or_404(Student, pk=pk)
    rows = _assignments_qs().filter(student=student)
    return {
        "active": [assignment_json(a) for a in rows if a.status == AssignmentStatus.ACTIVE],
        "history": [assignment_json(a) for a in rows if a.status != AssignmentStatus.ACTIVE],
        "monthly_total": str(
            sum(a.group.monthly_fee for a in rows if a.status == AssignmentStatus.ACTIVE)
        ),
    }


@ajax(methods=["GET", "POST"], perm="academics.view_group")
def group_students(request, pk):
    group = get_object_or_404(visible_groups(request.user, Group.objects.all()), pk=pk)

    if request.method == "GET":
        rows = _assignments_qs().filter(group=group, status=AssignmentStatus.ACTIVE)
        return {
            "results": [assignment_json(a) for a in rows],
            "count": len(rows),
            "capacity": group.capacity,
            "is_full": svc.group_is_full(group),
        }

    if not request.user.has_perm("students.add_studentgroupassignment"):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

    student = get_object_or_404(Student, pk=request.json.get("student_id"))
    assignment, warnings = svc.assign_student(
        student,
        group,
        actor=request.user,
        start_date=request.json.get("start_date") or None,
        is_default=request.json.get("is_default", True),
        notes=request.json.get("notes", ""),
        allow_grade_mismatch=bool(request.json.get("allow_grade_mismatch")),
    )
    return {"assignment": assignment_json(assignment), "warnings": warnings}


@ajax(methods=["DELETE"], perm="students.change_studentgroupassignment")
def group_student_detail(request, pk, student_id):
    """Soft by design: ends the assignment, never deletes the row."""
    group = get_object_or_404(visible_groups(request.user, Group.objects.all()), pk=pk)
    assignment = get_object_or_404(
        _assignments_qs(), group=group, student_id=student_id, status=AssignmentStatus.ACTIVE
    )
    svc.end_assignment(
        assignment,
        actor=request.user,
        end_date=request.json.get("end_date") or None,
        reason=request.json.get("reason") or "LEFT_CENTER",
        notes=request.json.get("notes", ""),
    )
    return {"assignment": assignment_json(assignment)}


@ajax(methods=["POST"], perm="students.change_studentgroupassignment")
def transfer(request, pk):
    assignment = get_object_or_404(_assignments_qs(), pk=pk)
    new_group = get_object_or_404(Group, pk=request.json.get("new_group_id"))
    new_assignment, warnings = svc.transfer_student(
        assignment,
        new_group,
        actor=request.user,
        start_date=request.json.get("start_date") or None,
        reason=request.json.get("reason", ""),
    )
    return {"assignment": assignment_json(new_assignment), "warnings": warnings}
