"""Student services (TASK-021, TASK-022, TASK-025).

Every write here is transactional and audited; views never touch the model
directly.
"""

from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

from apps.core.audit import diff, record, snapshot
from apps.core.http import DomainError
from apps.core.models import AuditAction
from apps.core.sequences import format_code, next_number
from apps.core.text import normalize_arabic
from apps.tenancy import quota

from .models import Student, StudentStatus

STUDENT_CODE_KEY = "student_code"
STUDENT_CODE_PREFIX = "STD-"

AUDITED_FIELDS = [
    "full_name",
    "phone",
    "guardian_name",
    "guardian_phone",
    "guardian_relation",
    "address",
    "grade",
    "school",
    "status",
    "date_of_birth",
    "gender",
    "enrolled_on",
    "notes",
]


def next_student_code() -> str:
    return format_code(STUDENT_CODE_PREFIX, next_number(STUDENT_CODE_KEY))


@transaction.atomic
def create_student(*, actor=None, reason: str = "", **fields) -> Student:
    """Create a student, allocating a unique code with a bounded retry."""
    quota.check("students")
    for attempt in range(5):
        code = fields.get("student_code") or next_student_code()
        try:
            with transaction.atomic():
                student = Student.objects.create(
                    **{**fields, "student_code": code, "created_by": actor, "updated_by": actor}
                )
            break
        except IntegrityError:
            if fields.get("student_code") or attempt == 4:
                raise DomainError(
                    "ERR_DUPLICATE_CODE",
                    _("كود الطالب مستخدم بالفعل"),
                    status=409,
                    field_errors={"student_code": [_("كود مستخدم بالفعل")]},
                ) from None
    record(
        AuditAction.STUDENT_CREATED,
        student,
        changes=snapshot(student, AUDITED_FIELDS),
        reason=reason,
        actor=actor,
    )
    return student


@transaction.atomic
def update_student(student: Student, *, actor=None, reason: str = "", **fields) -> Student:
    before = snapshot(student, AUDITED_FIELDS)
    for name, value in fields.items():
        setattr(student, name, value)
    student.updated_by = actor
    student.save()

    changes = diff(before, snapshot(student, AUDITED_FIELDS))
    if changes:
        record(
            AuditAction.STUDENT_UPDATED,
            student,
            changes=changes,
            reason=reason,
            actor=actor,
        )
    return student


@transaction.atomic
def change_status(student: Student, new_status: str, *, actor=None, reason: str = "") -> Student:
    if new_status not in StudentStatus.values:
        raise DomainError(
            "ERR_VALIDATION",
            _("حالة غير معروفة"),
            field_errors={"status": [_("حالة غير معروفة")]},
        )
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب تغيير الحالة مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    old = student.status
    if old == new_status:
        return student

    student.status = new_status
    student.updated_by = actor
    student.save(update_fields=["status", "updated_by", "updated_at"])

    record(
        AuditAction.STUDENT_STATUS_CHANGED,
        student,
        changes={"status": {"old": old, "new": new_status}},
        reason=reason,
        actor=actor,
    )
    return student


def search_students(queryset, term: str):
    """Search by code, name (Arabic-normalised) or guardian phone."""
    from django.db.models import Q

    term = (term or "").strip()
    if not term:
        return queryset

    normalized = normalize_arabic(term)
    filters = (
        Q(student_code__icontains=term)
        | Q(search_name__icontains=normalized)
        | Q(guardian_phone__icontains=term)
        | Q(phone__icontains=term)
    )
    return queryset.filter(filters)
