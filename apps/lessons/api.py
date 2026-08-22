"""Lesson endpoints (TASK-042 → 045)."""

from datetime import date, datetime, timedelta

from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.models import Group
from apps.accounts.scoping import can_touch_lesson, visible_groups, visible_lessons
from apps.core.http import DomainError, ajax

from . import services
from .models import Lesson, LessonStatus

PAGE_SIZE = 50


def lesson_json(lesson: Lesson, *, detail=False) -> dict:
    local_start = timezone.localtime(lesson.scheduled_start)
    data = {
        "id": lesson.pk,
        "group_id": lesson.group_id,
        "group": lesson.group.name,
        "group_code": lesson.group.code,
        "subject": str(lesson.group.grade_subject.subject),
        "grade": str(lesson.group.grade_subject.grade),
        "instructor": str(lesson.instructor) if lesson.instructor_id else None,
        "lesson_date": lesson.lesson_date.isoformat(),
        "start": local_start.strftime("%H:%M"),
        "end": timezone.localtime(lesson.scheduled_end).strftime("%H:%M"),
        "status": lesson.status,
        "status_display": lesson.get_status_display(),
        "expected_students": lesson.expected_students,
    }
    if detail:
        data.update(
            {
                "check_in_opens_at": timezone.localtime(lesson.check_in_opens_at).strftime("%H:%M"),
                "check_in_closes_at": timezone.localtime(lesson.check_in_closes_at).strftime(
                    "%H:%M"
                ),
                "late_after": timezone.localtime(lesson.late_after).strftime("%H:%M"),
                "window_state": lesson.window_state(),
                "notes": lesson.notes,
                "cancel_reason": lesson.cancel_reason,
            }
        )
    return data


def _parse_date(value, default=None):
    if not value:
        return default
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise DomainError("ERR_VALIDATION", _("تاريخ غير صحيح")) from exc


def lesson_queryset(request):
    qs = visible_lessons(request.user, Lesson.objects.with_related())
    params = request.GET
    if params.get("group"):
        qs = qs.filter(group_id=params["group"])
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("instructor"):
        qs = qs.filter(group__instructor_id=params["instructor"])
    if params.get("from"):
        qs = qs.filter(lesson_date__gte=_parse_date(params["from"]))
    if params.get("to"):
        qs = qs.filter(lesson_date__lte=_parse_date(params["to"]))
    if params.get("scope") == "today":
        qs = qs.filter(lesson_date=timezone.localdate())
    elif params.get("scope") == "upcoming":
        qs = qs.filter(
            lesson_date__gte=timezone.localdate(), status=LessonStatus.SCHEDULED
        ).order_by("scheduled_start")
    return qs


@ajax(methods=["GET", "POST"], perm="lessons.view_lesson")
def lessons(request):
    if request.method == "GET":
        paginator = Paginator(lesson_queryset(request), PAGE_SIZE)
        page = paginator.get_page(request.GET.get("page") or 1)
        return {
            "results": [lesson_json(lesson) for lesson in page.object_list],
            "page": page.number,
            "pages": paginator.num_pages,
            "count": paginator.count,
            "has_next": page.has_next(),
            "has_previous": page.has_previous(),
        }

    if not request.user.has_perm("lessons.add_lesson"):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

    data = request.json
    group = get_object_or_404(visible_groups(request.user, Group.objects.all()), pk=data.get("group_id"))
    day = _parse_date(data.get("date"))
    if not day or not data.get("start") or not data.get("end"):
        raise DomainError(
            "ERR_VALIDATION",
            _("التاريخ ووقت البداية والنهاية مطلوبة"),
            field_errors={"date": [_("مطلوب")]},
        )

    start = services._localize(day, datetime.strptime(data["start"], "%H:%M").time())
    end = services._localize(day, datetime.strptime(data["end"], "%H:%M").time())
    lesson = services.create_lesson(group, start, end, actor=request.user, notes=data.get("notes", ""))
    return {"lesson": lesson_json(lesson, detail=True)}


@ajax(methods=["GET", "PATCH"], perm="lessons.view_lesson")
def lesson_detail(request, pk):
    lesson = get_object_or_404(lesson_queryset(request), pk=pk)
    if request.method == "GET":
        return {"lesson": lesson_json(lesson, detail=True)}

    if not (request.user.has_perm("lessons.change_lesson") and can_touch_lesson(request.user, lesson)):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)

    data = request.json
    fields = []
    if data.get("notes") is not None:
        lesson.notes = data["notes"]
        fields.append("notes")
    if data.get("instructor_id"):
        lesson.instructor_id = data["instructor_id"]
        fields.append("instructor")
    if fields:
        lesson.save(update_fields=[*fields, "updated_at"])
        services.invalidate_lesson_cache(lesson.pk)
    return {"lesson": lesson_json(lesson, detail=True)}


@ajax(methods=["POST"], perm="lessons.generate_lessons")
def generate(request):
    data = request.json
    group = get_object_or_404(
        visible_groups(request.user, Group.objects.all()), pk=data.get("group_id")
    )
    from_date = _parse_date(data.get("from"), timezone.localdate())
    to_date = _parse_date(data.get("to"), from_date + timedelta(days=30))
    holidays = {_parse_date(value) for value in data.get("holidays", []) if value}

    result = services.generate_lessons(
        group,
        from_date,
        to_date,
        actor=request.user,
        holidays=holidays,
        dry_run=bool(data.get("dry_run")),
    )
    return {
        "created": result["created"],
        "skipped": result["skipped"],
        "dry_run": bool(data.get("dry_run")),
        "reason": result.get("reason", ""),
    }


def _lifecycle(request, pk, permission, action):
    lesson = get_object_or_404(lesson_queryset(request), pk=pk)
    if not (request.user.has_perm(permission) and can_touch_lesson(request.user, lesson)):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا الإجراء"), status=403)
    return lesson, action


@ajax(methods=["POST"], perm="lessons.open_lesson")
def open_lesson(request, pk):
    lesson, _action = _lifecycle(request, pk, "lessons.open_lesson", "open")
    lesson = services.open_lesson(lesson, actor=request.user)
    return {"lesson": lesson_json(lesson, detail=True)}


@ajax(methods=["POST"], perm="lessons.complete_lesson")
def complete_lesson(request, pk):
    lesson, _action = _lifecycle(request, pk, "lessons.complete_lesson", "complete")
    lesson, summary = services.complete_lesson(lesson, actor=request.user)
    return {"lesson": lesson_json(lesson, detail=True), "summary": summary}


@ajax(methods=["POST"], perm="lessons.cancel_lesson")
def cancel_lesson(request, pk):
    lesson, _action = _lifecycle(request, pk, "lessons.cancel_lesson", "cancel")
    lesson = services.cancel_lesson(lesson, actor=request.user, reason=request.json.get("reason", ""))
    return {"lesson": lesson_json(lesson, detail=True)}


@ajax(methods=["GET"], perm="lessons.view_lesson")
def active_lessons(request):
    """Lesson picker for the scanner console."""
    today: date = timezone.localdate()
    qs = visible_lessons(request.user, Lesson.objects.with_related()).filter(
        status=LessonStatus.OPEN
    ).order_by("scheduled_start")
    scheduled_today = (
        visible_lessons(request.user, Lesson.objects.with_related())
        .filter(status=LessonStatus.SCHEDULED, lesson_date=today)
        .order_by("scheduled_start")
    )
    return {
        "open": [lesson_json(lesson) for lesson in qs],
        "scheduled_today": [lesson_json(lesson) for lesson in scheduled_today],
    }
