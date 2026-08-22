"""Lesson services (TASK-041 → 045)."""

from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.models import Weekday
from apps.core.audit import record
from apps.core.http import DomainError
from apps.core.models import AuditAction
from apps.core.registry import settings_registry

from .models import Lesson, LessonStatus

LESSON_CACHE_TTL = 300


# --------------------------------------------------------------------------- #
# Window resolution (TASK-041)
# --------------------------------------------------------------------------- #

def resolve_windows(group, scheduled_start, *, late_after_minutes=None):
    """Precedence: explicit value → group override → global setting.

    Returns ``(check_in_opens_at, check_in_closes_at, late_after)``. The values
    are frozen onto the lesson at creation, so changing a global setting later
    never rewrites lessons that already exist.
    """
    policies = settings_registry.get_many(
        "attendance.window_open_before_minutes",
        "attendance.window_close_after_minutes",
        "attendance.late_after_minutes",
    )
    if late_after_minutes is None:
        late_after_minutes = (
            group.default_late_after_minutes
            if group.default_late_after_minutes is not None
            else policies["attendance.late_after_minutes"]
        )

    opens = scheduled_start - timedelta(minutes=policies["attendance.window_open_before_minutes"])
    closes = scheduled_start + timedelta(minutes=policies["attendance.window_close_after_minutes"])
    late_after = scheduled_start + timedelta(minutes=late_after_minutes)
    return opens, closes, late_after


def _localize(day, clock: time):
    """Combine a local date and wall-clock time into an aware datetime."""
    naive = datetime.combine(day, clock)
    return timezone.make_aware(naive, timezone.get_current_timezone())


# --------------------------------------------------------------------------- #
# Creation (TASK-042)
# --------------------------------------------------------------------------- #

@transaction.atomic
def create_lesson(group, scheduled_start, scheduled_end, *, actor=None, instructor=None, notes=""):
    opens, closes, late_after = resolve_windows(group, scheduled_start)
    try:
        lesson = Lesson.objects.create(
            group=group,
            instructor=instructor or group.instructor,
            scheduled_start=scheduled_start,
            scheduled_end=scheduled_end,
            check_in_opens_at=opens,
            check_in_closes_at=closes,
            late_after=late_after,
            notes=notes,
            created_by=actor,
        )
    except IntegrityError as exc:
        raise DomainError(
            "ERR_LESSON_EXISTS",
            _("توجد حصة لهذه المجموعة في نفس التوقيت"),
            status=409,
        ) from exc

    record(AuditAction.LESSON_CREATED, lesson, reason=notes, actor=actor)
    return lesson


def generate_lessons(group, from_date, to_date, *, actor=None, holidays=(), dry_run=False):
    """Expand a group's weekly schedule into dated lessons.

    Idempotent by construction: existing ``(group, scheduled_start)`` pairs are
    skipped, so re-running over the same range creates nothing.
    """
    if to_date < from_date:
        raise DomainError("ERR_INVALID_RANGE", _("نطاق التواريخ غير صحيح"))
    if (to_date - from_date).days > 366:
        raise DomainError("ERR_RANGE_TOO_LONG", _("النطاق أطول من سنة"))

    schedules = list(group.schedules.filter(is_active=True))
    if not schedules:
        return {"created": 0, "skipped": 0, "lessons": [], "reason": "NO_SCHEDULE"}

    holidays = set(holidays)
    existing = set(
        Lesson.objects.filter(
            group=group, scheduled_start__gte=_localize(from_date, time.min)
        ).values_list("scheduled_start", flat=True)
    )

    created, skipped, lessons = 0, 0, []
    day = from_date
    while day <= to_date:
        weekday = Weekday.from_python_weekday(day.weekday())
        for schedule in schedules:
            if schedule.weekday != weekday:
                continue
            if day in holidays:
                skipped += 1
                continue

            start = _localize(day, schedule.start_time)
            end = _localize(day, schedule.end_time)
            if start in existing:
                skipped += 1
                continue
            if dry_run:
                created += 1
                lessons.append({"start": start.isoformat(), "end": end.isoformat()})
                continue

            lesson = create_lesson(group, start, end, actor=actor)
            existing.add(start)
            created += 1
            lessons.append(lesson)
        day += timedelta(days=1)

    return {"created": created, "skipped": skipped, "lessons": lessons}


# --------------------------------------------------------------------------- #
# Lifecycle (TASK-043)
# --------------------------------------------------------------------------- #

@transaction.atomic
def open_lesson(lesson, *, actor=None):
    if lesson.status == LessonStatus.OPEN:
        return lesson  # idempotent
    if lesson.status != LessonStatus.SCHEDULED:
        raise DomainError(
            "ERR_LESSON_STATE",
            _("لا يمكن فتح حصة %(state)s") % {"state": lesson.get_status_display()},
            status=409,
        )

    from apps.students.models import AssignmentStatus, StudentGroupAssignment

    lesson.status = LessonStatus.OPEN
    lesson.actual_start_at = timezone.now()
    lesson.expected_students = StudentGroupAssignment.objects.filter(
        group=lesson.group, status=AssignmentStatus.ACTIVE
    ).count()
    lesson.save(
        update_fields=["status", "actual_start_at", "expected_students", "updated_at"]
    )
    invalidate_lesson_cache(lesson.pk)
    record(AuditAction.LESSON_OPENED, lesson, actor=actor)
    return lesson


@transaction.atomic
def complete_lesson(lesson, *, actor=None):
    """Close the lesson, then hand over to the attendance sweep (TASK-055)."""
    if lesson.status == LessonStatus.COMPLETED:
        return lesson, {"auto_checked_out": 0, "absences": 0}
    if lesson.status != LessonStatus.OPEN:
        raise DomainError(
            "ERR_LESSON_STATE",
            _("لا يمكن إنهاء حصة %(state)s") % {"state": lesson.get_status_display()},
            status=409,
        )

    lesson.status = LessonStatus.COMPLETED
    lesson.actual_end_at = timezone.now()
    lesson.save(update_fields=["status", "actual_end_at", "updated_at"])
    invalidate_lesson_cache(lesson.pk)

    from apps.attendance.services import finalize_lesson

    summary = finalize_lesson(lesson, actor=actor)
    record(AuditAction.LESSON_COMPLETED, lesson, changes=summary, actor=actor)
    return lesson, summary


@transaction.atomic
def cancel_lesson(lesson, *, actor=None, reason: str = ""):
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب الإلغاء مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    if lesson.status == LessonStatus.COMPLETED:
        raise DomainError("ERR_LESSON_STATE", _("لا يمكن إلغاء حصة منتهية"), status=409)

    previous = lesson.status
    lesson.status = LessonStatus.CANCELLED
    lesson.cancel_reason = reason
    lesson.save(update_fields=["status", "cancel_reason", "updated_at"])
    invalidate_lesson_cache(lesson.pk)

    record(
        AuditAction.LESSON_CANCELLED,
        lesson,
        changes={"status": {"old": previous, "new": LessonStatus.CANCELLED}},
        reason=reason,
        actor=actor,
    )
    return lesson


# --------------------------------------------------------------------------- #
# Scan-path snapshot (TASK-045)
# --------------------------------------------------------------------------- #

def _cache_key(lesson_id: int) -> str:
    return f"lesson:{lesson_id}:snapshot"


def lesson_snapshot(lesson_id: int) -> dict | None:
    """Immutable-ish lesson facts the scan path needs, cached.

    Saves one query per scan. Any state change busts the key, so a cancelled
    lesson can never keep accepting scans from a stale cache.
    """
    cached = cache.get(_cache_key(lesson_id))
    if cached is not None:
        return cached

    lesson = (
        Lesson.objects.select_related(
            "group__grade_subject__subject", "group__grade_subject__grade"
        )
        .filter(pk=lesson_id)
        .first()
    )
    if lesson is None:
        return None

    snapshot = {
        "id": lesson.pk,
        "group_id": lesson.group_id,
        "group_name": lesson.group.name,
        "grade_subject_id": lesson.group.grade_subject_id,
        "grade_id": lesson.group.grade_subject.grade_id,
        "subject": str(lesson.group.grade_subject.subject),
        "status": lesson.status,
        "scheduled_start": lesson.scheduled_start,
        "check_in_opens_at": lesson.check_in_opens_at,
        "check_in_closes_at": lesson.check_in_closes_at,
        "late_after": lesson.late_after,
        "capacity": lesson.group.capacity,
    }
    cache.set(_cache_key(lesson_id), snapshot, LESSON_CACHE_TTL)
    return snapshot


def invalidate_lesson_cache(lesson_id: int) -> None:
    cache.delete(_cache_key(lesson_id))


def window_state(snapshot: dict, now=None) -> str:
    from .models import WindowState

    now = now or timezone.now()
    if now < snapshot["check_in_opens_at"]:
        return WindowState.BEFORE_OPEN
    if now > snapshot["check_in_closes_at"]:
        return WindowState.AFTER_CLOSE
    return WindowState.OPEN
