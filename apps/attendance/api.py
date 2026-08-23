"""Attendance endpoints (TASK-053, TASK-054, TASK-057, TASK-058)."""

from datetime import datetime

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.accounts.scoping import can_touch_lesson, visible_lessons, visible_students
from apps.core import ratelimit
from apps.core.http import DomainError, ajax, fail, ok
from apps.lessons.models import Lesson
from apps.students.models import Student

from . import services
from .models import (
    Attendance,
    AttendanceEvent,
    AttendanceState,
    AttendanceStatus,
    AttendanceType,
    ScanSource,
)

MAX_TOKEN_CHARS = 64


def _lesson_for_scan(request, lesson_id):
    lesson = get_object_or_404(visible_lessons(request.user, Lesson.objects.all()), pk=lesson_id)
    if not can_touch_lesson(request.user, lesson):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذه الحصة"), status=403)
    return lesson


@ajax(methods=["POST"], perm="attendance.add_attendance", feature="attendance.qr")
def scan(request):
    """★ The hot path. Validation happens before any database work."""
    data = request.json
    lesson_id = data.get("lesson_id")
    token = data.get("qr_token") or ""

    if not isinstance(lesson_id, int) or lesson_id < 1:
        raise DomainError(
            "ERR_VALIDATION",
            _("رقم الحصة مطلوب"),
            field_errors={"lesson_id": [_("مطلوب")]},
        )
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS:
        raise DomainError("ERR_CARD_NOT_FOUND", _("رمز البطاقة غير صالح"), status=400)

    device_id = str(data.get("device_id") or "")[:60]
    ratelimit.check("scan_device", device_id or "unknown", limit=10, window=1)
    ratelimit.check("scan_user", str(request.user.pk), limit=600, window=60)

    _lesson_for_scan(request, lesson_id)  # permission + scoping, cheap

    approve = bool(data.get("approve"))
    if approve and not request.user.has_perm("attendance.approve_exceptional"):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية الاعتماد"), status=403)

    try:
        result = services.scan(
            lesson_id=lesson_id,
            qr_token=token,
            actor=request.user,
            device_id=device_id,
            idempotency_key=(data.get("idempotency_key") or None),
            intent=data.get("intent") or "AUTO",
            scan_source=data.get("scan_source") or ScanSource.HID,
            makeup_for_lesson_id=data.get("makeup_for_lesson_id"),
            approve=approve,
        )
    except services.ScanRejected as exc:
        # Approval-needed responses are not failures: the operator may escalate.
        payload = {"code": exc.code, "message": exc.message, **exc.data}
        payload.update(
            {
                "severity": exc.info["severity"],
                "colour": exc.info["colour"],
                "sound": exc.info["sound"],
            }
        )
        if exc.info["severity"] == "NEEDS_APPROVAL":
            return ok(payload, code=exc.code, message=exc.message)
        return fail(exc.code, exc.message, status=exc.info["http_status"], data=payload)

    if not request.user.has_perm("payments.view_monthlycharge"):
        result.pop("payment", None)
    return ok(result, code=result["code"], message=result["message"])


@ajax(methods=["POST"], perm="attendance.add_attendance")
def checkout(request, pk):
    attendance = get_object_or_404(Attendance.objects.select_related("lesson"), pk=pk)
    _lesson_for_scan(request, attendance.lesson_id)
    if attendance.state != AttendanceState.CHECKED_IN:
        raise DomainError("ERR_NOT_CHECKED_IN", _("لا يوجد حضور مفتوح"), status=409)

    now = timezone.now()
    services.manual_adjust(
        attendance,
        actor=request.user,
        reason=request.json.get("reason") or "انصراف يدوي",
        check_out_at=now,
    )
    attendance.state = AttendanceState.CHECKED_OUT
    attendance.save(update_fields=["state", "updated_at"])
    return {"attendance": attendance_json(attendance)}


@ajax(methods=["PATCH"], perm="attendance.correct_attendance")
def attendance_detail(request, pk):
    attendance = get_object_or_404(
        Attendance.objects.select_related("lesson__group", "student"), pk=pk
    )
    _lesson_for_scan(request, attendance.lesson_id)

    data = request.json
    fields = {}
    for name in ("status", "attendance_type", "notes"):
        if name in data:
            fields[name] = data[name]
    for name in ("check_in_at", "check_out_at"):
        if data.get(name):
            fields[name] = _parse_datetime(data[name])
    if "makeup_for_lesson_id" in data:
        fields["makeup_for_lesson_id"] = data["makeup_for_lesson_id"]
        if data["makeup_for_lesson_id"]:
            fields["attendance_type"] = AttendanceType.MAKEUP

    attendance = services.manual_adjust(
        attendance, actor=request.user, reason=data.get("reason", ""), **fields
    )
    return {"attendance": attendance_json(attendance)}


@ajax(methods=["POST"], perm="attendance.correct_attendance")
def cancel(request, pk):
    attendance = get_object_or_404(Attendance.objects.select_related("lesson"), pk=pk)
    _lesson_for_scan(request, attendance.lesson_id)
    attendance = services.cancel_attendance(
        attendance, actor=request.user, reason=request.json.get("reason", "")
    )
    return {"attendance": attendance_json(attendance)}


@ajax(methods=["POST"], perm="attendance.add_attendance", feature="attendance.manual")
def manual(request):
    data = request.json
    lesson = _lesson_for_scan(request, data.get("lesson_id"))
    student = get_object_or_404(Student, pk=data.get("student_id"))
    attendance = services.manual_create(
        lesson, student, actor=request.user, reason=data.get("reason", "")
    )
    return {"attendance": attendance_json(attendance)}


@ajax(methods=["POST"], perm="attendance.approve_exceptional", feature="attendance.exceptional")
def approve(request, pk):
    attendance = get_object_or_404(Attendance.objects.select_related("lesson"), pk=pk)
    _lesson_for_scan(request, attendance.lesson_id)
    attendance.requires_approval = True
    attendance.approved_by = request.user
    attendance.approved_at = timezone.now()
    attendance.save(update_fields=["requires_approval", "approved_by", "approved_at", "updated_at"])

    from apps.core.audit import record
    from apps.core.models import AuditAction

    record(
        AuditAction.ATTENDANCE_APPROVED,
        attendance,
        reason=request.json.get("reason", ""),
        actor=request.user,
    )
    return {"attendance": attendance_json(attendance)}


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def attendance_json(attendance: Attendance) -> dict:
    return {
        "id": attendance.pk,
        "student_id": attendance.student_id,
        "student": attendance.student.full_name,
        "student_code": attendance.student.student_code,
        "state": attendance.state,
        "state_display": attendance.get_state_display(),
        "status": attendance.status,
        "status_display": attendance.get_status_display(),
        "type": attendance.attendance_type,
        "type_display": attendance.get_attendance_type_display(),
        "assigned_group": attendance.assigned_group.name if attendance.assigned_group_id else None,
        "attended_group": attendance.attended_group.name,
        "check_in": (
            timezone.localtime(attendance.check_in_at).strftime("%H:%M")
            if attendance.check_in_at
            else None
        ),
        "check_out": (
            timezone.localtime(attendance.check_out_at).strftime("%H:%M")
            if attendance.check_out_at
            else None
        ),
        "late_minutes": attendance.late_minutes,
        "duration_minutes": attendance.duration_minutes,
        "is_manual": attendance.is_manual,
        "is_alternative": attendance.is_alternative,
    }


def _parse_datetime(value):
    # A "+00:00" offset arrives as " 00:00" when a client forgets to encode the
    # query string; be tolerant rather than 400 on a cursor round-trip.
    if isinstance(value, str) and " " in value[10:]:
        value = value[:10] + value[10:].replace(" ", "+", 1)
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise DomainError("ERR_VALIDATION", _("تاريخ/وقت غير صحيح")) from exc
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


@ajax(methods=["GET"], perm="attendance.view_attendance")
def lesson_attendance(request, pk):
    """Roster + live counters for the lesson dashboard (§37)."""
    lesson = get_object_or_404(visible_lessons(request.user, Lesson.objects.with_related()), pk=pk)
    rows = Attendance.objects.with_related().filter(lesson=lesson)

    counters = rows.aggregate(
        present=Count("id", filter=Q(status=AttendanceStatus.PRESENT)),
        late=Count("id", filter=Q(status=AttendanceStatus.LATE)),
        absent=Count("id", filter=Q(state=AttendanceState.ABSENT)),
        partial=Count("id", filter=Q(status=AttendanceStatus.PARTIAL)),
        inside=Count("id", filter=Q(state=AttendanceState.CHECKED_IN)),
        alternative=Count(
            "id",
            filter=~Q(attendance_type=AttendanceType.NORMAL) & ~Q(state=AttendanceState.ABSENT),
        ),
    )
    counters["expected"] = lesson.expected_students
    counters["recorded"] = rows.count()

    from apps.students.models import AssignmentStatus, StudentGroupAssignment

    recorded_ids = set(rows.values_list("student_id", flat=True))
    not_arrived = [
        {
            "student_id": a.student_id,
            "student": a.student.full_name,
            "student_code": a.student.student_code,
        }
        for a in StudentGroupAssignment.objects.select_related("student").filter(
            group=lesson.group, status=AssignmentStatus.ACTIVE
        )
        if a.student_id not in recorded_ids
    ]

    return {
        "lesson": {
            "id": lesson.pk,
            "status": lesson.status,
            "group": lesson.group.name,
            "subject": str(lesson.group.grade_subject.subject),
            "date": lesson.lesson_date.isoformat(),
        },
        "counters": counters,
        "results": [attendance_json(a) for a in rows],
        "not_arrived": not_arrived,
    }


@ajax(methods=["GET"], perm="attendance.view_attendance")
def lesson_feed(request, pk):
    """Incremental event feed for the live board (5 s polling)."""
    lesson = get_object_or_404(visible_lessons(request.user, Lesson.objects.all()), pk=pk)
    since = request.GET.get("since")
    events = AttendanceEvent.objects.filter(lesson=lesson).select_related("student")
    if since:
        events = events.filter(created_at__gt=_parse_datetime(since))

    rows = list(events.order_by("-created_at")[:50])
    return {
        "results": [
            {
                "id": event.pk,
                "at": timezone.localtime(event.created_at).strftime("%H:%M:%S"),
                "created_at": event.created_at.isoformat(),
                "student": event.student.full_name if event.student_id else "—",
                "event_type": event.event_type,
                "code": event.result_code,
                "message": event.message,
                "device": event.device_id,
            }
            for event in rows
        ],
        "cursor": rows[0].created_at.isoformat() if rows else since,
    }


@ajax(methods=["GET"], perm="students.view_student")
def student_attendance(request, pk):
    """Student attendance history (§34)."""
    student = get_object_or_404(visible_students(request.user, Student.objects.all()), pk=pk)
    rows = (
        Attendance.objects.with_related()
        .filter(student=student)
        .select_related("lesson__group__grade_subject__subject")
        .order_by("-lesson__lesson_date", "-check_in_at")
    )
    params = request.GET
    if params.get("subject"):
        rows = rows.filter(lesson__group__grade_subject__subject_id=params["subject"])
    if params.get("type"):
        rows = rows.filter(attendance_type=params["type"])
    if params.get("month"):
        year, month = params["month"].split("-")
        rows = rows.filter(lesson__lesson_date__year=year, lesson__lesson_date__month=month)

    paginator = Paginator(rows, 25)
    page = paginator.get_page(params.get("page") or 1)
    return {
        "results": [
            {
                **attendance_json(a),
                "lesson_id": a.lesson_id,
                "date": a.lesson.lesson_date.isoformat(),
                "subject": str(a.lesson.group.grade_subject.subject),
            }
            for a in page.object_list
        ],
        "page": page.number,
        "pages": paginator.num_pages,
        "count": paginator.count,
        "has_next": page.has_next(),
        "has_previous": page.has_previous(),
    }
