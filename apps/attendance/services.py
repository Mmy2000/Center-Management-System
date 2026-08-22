"""The scan path (docs/03 §D.5, §E.5) and the lesson-completion sweep.

Design rules that hold everywhere in this module:
  * ~6 queries, one transaction, no template rendering — this is the hot path;
  * gates run cheapest-first and ordered by likelihood of failure;
  * the payment gate costs *zero* queries while it is switched off;
  * nothing here ever writes StudentGroupAssignment (Principle 2).
"""

import logging
import time as time_module
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.cards import services as card_services
from apps.cards.models import CardStatus
from apps.core.audit import record
from apps.core.http import DomainError
from apps.core.models import AuditAction
from apps.core.policies import BLOCK, REQUIRE_APPROVAL, WARN
from apps.core.registry import settings_registry
from apps.lessons.models import LessonStatus, WindowState
from apps.lessons.services import lesson_snapshot, window_state
from apps.students.models import StudentStatus

from . import eligibility as elig
from . import result_codes
from .models import (
    Attendance,
    AttendanceEvent,
    AttendanceEventType,
    AttendanceState,
    AttendanceStatus,
    AttendanceType,
    ScanSource,
)

logger = logging.getLogger("scan")


class ScanRejected(DomainError):
    """A refusal that still deserves an AttendanceEvent row."""

    def __init__(self, code, *, data=None):
        info = result_codes.info(code)
        super().__init__(code, info["message"], status=info["http_status"], data=data or {})
        self.info = info


# --------------------------------------------------------------------------- #
# Result assembly
# --------------------------------------------------------------------------- #

def _student_payload(student) -> dict:
    return {
        "id": student.pk,
        "code": student.student_code,
        "name": student.full_name,
        "photo": student.photo.url if student.photo else None,
    }


def _attendance_payload(attendance: Attendance) -> dict:
    return {
        "id": attendance.pk,
        "state": attendance.state,
        "status": attendance.status,
        "type": attendance.attendance_type,
        "check_in": timezone.localtime(attendance.check_in_at).strftime("%H:%M:%S")
        if attendance.check_in_at
        else None,
        "check_out": timezone.localtime(attendance.check_out_at).strftime("%H:%M:%S")
        if attendance.check_out_at
        else None,
        "late_minutes": attendance.late_minutes,
        "duration_minutes": attendance.duration_minutes,
    }


# --------------------------------------------------------------------------- #
# Gate 7 — the one optional bridge to money (docs/04 §F.7)
# --------------------------------------------------------------------------- #

def _payment_gate(student, snapshot) -> tuple[str | None, dict | None]:
    """Returns ``(code_or_None, payment_payload_or_None)``.

    Costs zero queries while ``payments.enforce_on_attendance`` is off, which is
    the default: turning a paying customer away because of a config default is a
    business-losing failure mode.
    """
    if not settings_registry.get("payments.enforce_on_attendance"):
        return None, None

    from apps.payments.models import MonthlyCharge

    today = timezone.localdate()
    billing_month = today.replace(day=1)
    charge = (
        MonthlyCharge.objects.filter(
            student=student,
            grade_subject_id=snapshot["grade_subject_id"],
            billing_month=billing_month,
        )
        .only("amount_due", "discount_amount", "total_paid", "balance", "status")
        .first()
    )
    if charge is None:
        return None, None  # nothing billed yet

    payload = {
        "status": charge.status,
        "remaining": str(charge.balance),
        "month": billing_month.isoformat(),
        "charge_id": charge.pk,
    }
    grace_days = settings_registry.get("payments.grace_days")
    if today.day <= grace_days or charge.balance <= 0:
        return None, payload

    allow_unpaid = settings_registry.get("payments.allow_unpaid_attendance")
    allow_partial = settings_registry.get("payments.allow_partial_attendance")
    allowed = allow_partial if charge.total_paid > 0 else allow_unpaid
    if allowed:
        return "WARN_PAYMENT_DUE", payload

    action = settings_registry.get("payments.unpaid_block_action")
    if action == REQUIRE_APPROVAL:
        return "NEEDS_APPROVAL_PAYMENT", payload
    return "ERR_PAYMENT_BLOCKED", payload


# --------------------------------------------------------------------------- #
# The scan
# --------------------------------------------------------------------------- #

def scan(
    *,
    lesson_id: int,
    qr_token: str,
    actor=None,
    device_id: str = "",
    idempotency_key: str | None = None,
    intent: str = "AUTO",
    scan_source: str = ScanSource.HID,
    makeup_for_lesson_id: int | None = None,
    approve: bool = False,
) -> dict:
    started = time_module.monotonic()

    # Replay of a completed request returns the original answer verbatim.
    if idempotency_key:
        previous = AttendanceEvent.objects.filter(idempotency_key=idempotency_key).first()
        if previous is not None:
            payload = dict(previous.payload)
            payload["replayed"] = True
            return payload

    snapshot = lesson_snapshot(lesson_id)                                     # [Q1 cached]
    if snapshot is None:
        raise ScanRejected("ERR_LESSON_NOT_FOUND")

    card = None
    student = None
    try:
        card = card_services.find_card(qr_token)                              # [Q2]
        if card.status != CardStatus.ASSIGNED:
            raise ScanRejected(
                card_services.STATUS_ERRORS[card.status][0],
                data={"card_number": card.card_number},
            )
        student = card.current_student

        if student.status == StudentStatus.SUSPENDED:
            raise ScanRejected("ERR_STUDENT_SUSPENDED", data={"student": _student_payload(student)})
        if student.status != StudentStatus.ACTIVE:
            raise ScanRejected("ERR_STUDENT_INACTIVE", data={"student": _student_payload(student)})

        if snapshot["status"] == LessonStatus.CANCELLED:
            raise ScanRejected("ERR_LESSON_CANCELLED")
        if snapshot["status"] != LessonStatus.OPEN:
            raise ScanRejected("ERR_LESSON_NOT_OPEN")

        warnings: list[str] = []
        now = timezone.now()
        window = window_state(snapshot, now)
        if window != WindowState.OPEN:
            policy = settings_registry.get("attendance.outside_window_policy")
            if policy == BLOCK and not approve:
                raise ScanRejected("ERR_WINDOW_CLOSED")
            if policy == REQUIRE_APPROVAL and not approve:
                raise _approval_needed("NEEDS_APPROVAL_WINDOW", student, snapshot)
            warnings.append("WARN_OUTSIDE_WINDOW")

        assigned_group_id, assigned_group_name = elig.resolve_assigned_group(  # [Q3]
            student.pk, snapshot["grade_subject_id"]
        )
        decision = elig.classify(
            student=student,
            lesson_snapshot=snapshot,
            assigned_group_id=assigned_group_id,
            makeup_for_lesson_id=makeup_for_lesson_id,
        )
        if decision.decision == BLOCK and not approve:
            raise ScanRejected(decision.block_code, data={"student": _student_payload(student)})
        if decision.decision == REQUIRE_APPROVAL and not approve:
            raise _approval_needed(decision.code, student, snapshot, decision=decision)
        if decision.decision == WARN:
            warnings.append("WARN_NOT_ASSIGNED")

        payment_code, payment_payload = _payment_gate(student, snapshot)      # [Q4, usually skipped]
        if payment_code == "ERR_PAYMENT_BLOCKED" and not approve:
            raise ScanRejected("ERR_PAYMENT_BLOCKED", data={"payment": payment_payload})
        if payment_code == "NEEDS_APPROVAL_PAYMENT" and not approve:
            raise _approval_needed(
                "NEEDS_APPROVAL_PAYMENT", student, snapshot, extra={"payment": payment_payload}
            )
        if payment_code == "WARN_PAYMENT_DUE":
            warnings.append("WARN_PAYMENT_DUE")

        result = _write_attendance(
            snapshot=snapshot,
            student=student,
            card=card,
            decision=decision,
            actor=actor,
            intent=intent,
            scan_source=scan_source,
            makeup_for_lesson_id=makeup_for_lesson_id,
            approved=approve,
            now=now,
            warnings=warnings,
            outside_window=window != WindowState.OPEN,
            assigned_group_name=assigned_group_name,
        )
        if payment_payload:
            result["payment"] = payment_payload
        result["latency_ms"] = int((time_module.monotonic() - started) * 1000)

        _log_event(
            snapshot=snapshot,
            attendance_id=result["attendance"]["id"],
            student=student,
            card=card,
            event_type=result["_event_type"],
            code=result["code"],
            message=result["message"],
            device_id=device_id,
            operator=actor,
            idempotency_key=idempotency_key,
            latency_ms=result["latency_ms"],
            payload=result,
        )
        result.pop("_event_type", None)
        logger.info(
            "scan %s lesson=%s student=%s device=%s %sms",
            result["code"], lesson_id, student.pk, device_id, result["latency_ms"],
        )
        return result

    except DomainError as exc:
        _log_event(
            snapshot=snapshot,
            attendance_id=None,
            student=student,
            card=card,
            event_type=AttendanceEventType.DENIED,
            code=exc.code,
            message=exc.message,
            device_id=device_id,
            operator=actor,
            idempotency_key=None,  # refusals never consume the key
            latency_ms=int((time_module.monotonic() - started) * 1000),
            payload={"code": exc.code, "data": exc.data},
        )
        raise


def _approval_needed(code, student, snapshot, *, decision=None, extra=None) -> ScanRejected:
    data = {
        "student": _student_payload(student),
        "requires_approval": True,
        "groups": {
            "assigned": decision.assigned_group_id if decision else None,
            "attended": snapshot["group_name"],
        },
    }
    if extra:
        data.update(extra)
    rejection = ScanRejected(code, data=data)
    rejection.status = 200  # not an error: the operator can escalate
    return rejection


def _write_attendance(
    *,
    snapshot,
    student,
    card,
    decision,
    actor,
    intent,
    scan_source,
    makeup_for_lesson_id,
    approved,
    now,
    warnings,
    outside_window,
    assigned_group_name=None,
):
    duplicate_window = settings_registry.get("attendance.duplicate_window_seconds")
    min_gap = settings_registry.get("attendance.min_checkout_gap_minutes")
    partial_minutes = settings_registry.get("attendance.partial_min_duration_minutes")

    with transaction.atomic():
        defaults = {
            "assigned_group_id": decision.assigned_group_id,
            "attended_group_id": snapshot["group_id"],
            "state": AttendanceState.CHECKED_IN,
            "status": AttendanceStatus.PRESENT,
            "attendance_type": (
                AttendanceType.EXCEPTIONAL
                if (outside_window and decision.attendance_type == AttendanceType.NORMAL)
                else decision.attendance_type
            ),
            "check_in_at": now,
            "card_used": card,
            "checked_in_by": actor,
            "scan_source": scan_source,
            "makeup_for_lesson_id": makeup_for_lesson_id,
            "requires_approval": approved,
            "approved_by": actor if approved else None,
            "approved_at": now if approved else None,
        }
        if now > snapshot["late_after"]:
            defaults["status"] = AttendanceStatus.LATE
        defaults["late_minutes"] = max(
            0, int((now - snapshot["scheduled_start"]).total_seconds() // 60)
        )

        if intent == "CHECK_OUT":
            attendance = (
                Attendance.objects.select_for_update()
                .filter(lesson_id=snapshot["id"], student=student)
                .first()
            )
            if attendance is None or attendance.state == AttendanceState.ABSENT:
                raise ScanRejected("ERR_NOT_CHECKED_IN", data={"student": _student_payload(student)})
            created = False
        else:
            try:
                attendance, created = Attendance.objects.get_or_create(       # [Q5]
                    lesson_id=snapshot["id"], student=student, defaults=defaults
                )
            except IntegrityError:
                # Lost a race against another scanner: treat as a duplicate.
                attendance = Attendance.objects.get(lesson_id=snapshot["id"], student=student)
                created = False

        if created:
            code = decision.code
            if attendance.status == AttendanceStatus.LATE and code == "OK_CHECK_IN":
                code = "OK_CHECK_IN_LATE"
            return _result(
                attendance, code, "CHECK_IN", student, snapshot, decision, warnings,
                event_type=AttendanceEventType.CHECK_IN,
                assigned_group_name=assigned_group_name,
            )

        # Row already exists — decide duplicate vs check-out.
        attendance = (
            Attendance.objects.select_for_update()
            .select_related("assigned_group", "attended_group")
            .get(pk=attendance.pk)
        )

        if attendance.state == AttendanceState.CANCELLED:
            raise ScanRejected("ERR_ATTENDANCE_CANCELLED")

        if attendance.state == AttendanceState.ABSENT:
            # Materialised absence corrected by a real scan.
            attendance.state = AttendanceState.CHECKED_IN
            attendance.status = defaults["status"]
            attendance.check_in_at = now
            attendance.late_minutes = defaults["late_minutes"]
            attendance.attendance_type = defaults["attendance_type"]
            attendance.assigned_group_id = decision.assigned_group_id
            attendance.card_used = card
            attendance.checked_in_by = actor
            attendance.scan_source = scan_source
            attendance.save()
            return _result(
                attendance, decision.code, "CHECK_IN", student, snapshot, decision, warnings,
                event_type=AttendanceEventType.CHECK_IN,
                assigned_group_name=assigned_group_name,
            )

        if attendance.state == AttendanceState.CHECKED_OUT:
            return _result(
                attendance, "WARN_ALREADY_CHECKED_OUT", "NONE", student, snapshot, decision, warnings,
                event_type=AttendanceEventType.DUPLICATE,
                assigned_group_name=assigned_group_name,
            )

        elapsed = now - attendance.check_in_at
        if elapsed < timedelta(seconds=duplicate_window) or elapsed < timedelta(minutes=min_gap):
            return _result(
                attendance, "WARN_DUPLICATE", "NONE", student, snapshot, decision, warnings,
                event_type=AttendanceEventType.DUPLICATE,
                assigned_group_name=assigned_group_name,
            )

        attendance.state = AttendanceState.CHECKED_OUT
        attendance.check_out_at = now
        attendance.duration_minutes = int(elapsed.total_seconds() // 60)
        attendance.checked_out_by = actor
        if attendance.duration_minutes < partial_minutes:
            attendance.status = AttendanceStatus.PARTIAL
        attendance.save(
            update_fields=[
                "state", "check_out_at", "duration_minutes", "checked_out_by",
                "status", "updated_at",
            ]
        )
        code = (
            "OK_CHECK_OUT_PARTIAL"
            if attendance.status == AttendanceStatus.PARTIAL
            else "OK_CHECK_OUT"
        )
        return _result(
            attendance, code, "CHECK_OUT", student, snapshot, decision, warnings,
            event_type=AttendanceEventType.CHECK_OUT,
            assigned_group_name=assigned_group_name,
        )


def _result(
    attendance, code, action, student, snapshot, decision, warnings, *,
    event_type, assigned_group_name=None,
):
    info = result_codes.info(code)
    # Never dereference attendance.assigned_group here: that would cost the hot
    # path an extra query purely to render a name we already know.
    assigned_name = assigned_group_name
    return {
        "code": code,
        "severity": info["severity"],
        "message": info["message"],
        "colour": info["colour"],
        "sound": info["sound"],
        "action": action,
        "student": _student_payload(student),
        "attendance": _attendance_payload(attendance),
        "groups": {"assigned": assigned_name, "attended": snapshot["group_name"]},
        "warnings": warnings,
        "scenario": decision.scenario,
        "_event_type": event_type,
    }


def _log_event(
    *,
    snapshot,
    attendance_id,
    student,
    card,
    event_type,
    code,
    message,
    device_id,
    operator,
    idempotency_key,
    latency_ms,
    payload,
):
    if snapshot is None:
        return
    safe_payload = {k: v for k, v in payload.items() if not str(k).startswith("_")}
    AttendanceEvent.objects.create(                                            # [Q6]
        attendance_id=attendance_id,
        lesson_id=snapshot["id"],
        student=student,
        card=card,  # the card, never the token
        event_type=event_type,
        result_code=code,
        message=str(message)[:200],
        device_id=device_id[:60],
        operator=operator,
        idempotency_key=idempotency_key,
        latency_ms=latency_ms,
        payload=safe_payload,
    )


# --------------------------------------------------------------------------- #
# Manual corrections (TASK-054)
# --------------------------------------------------------------------------- #

EDITABLE_FIELDS = [
    "check_in_at",
    "check_out_at",
    "status",
    "attendance_type",
    "notes",
    "makeup_for_lesson_id",
]


@transaction.atomic
def manual_adjust(attendance: Attendance, *, actor, reason: str, **fields) -> Attendance:
    """The only way to change an attendance row by hand — reason mandatory."""
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب التعديل مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    from apps.core.audit import diff
    from apps.core.audit import snapshot as snap

    before = snap(attendance, EDITABLE_FIELDS)
    for name, value in fields.items():
        if name in EDITABLE_FIELDS:
            setattr(attendance, name, value)

    attendance.is_manual = True
    if attendance.check_in_at and attendance.check_out_at:
        attendance.duration_minutes = int(
            (attendance.check_out_at - attendance.check_in_at).total_seconds() // 60
        )
    if attendance.check_in_at and attendance.state == AttendanceState.ABSENT:
        attendance.state = AttendanceState.CHECKED_IN
    attendance.save()

    changes = diff(before, snap(attendance, EDITABLE_FIELDS))
    record(
        AuditAction.ATTENDANCE_MODIFIED,
        attendance,
        changes=changes,
        reason=reason,
        actor=actor,
    )
    AttendanceEvent.objects.create(
        attendance=attendance,
        lesson_id=attendance.lesson_id,
        student_id=attendance.student_id,
        event_type=AttendanceEventType.MANUAL_EDIT,
        result_code="MANUAL_EDIT",
        message=reason[:200],
        operator=actor,
        payload={"changes": changes},
    )
    return attendance


@transaction.atomic
def manual_create(lesson, student, *, actor, reason: str, attendance_type=AttendanceType.MANUAL):
    """Attendance without a card (forgotten card, broken scanner)."""
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب التسجيل اليدوي مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    now = timezone.now()
    assigned_group_id, _name = elig.resolve_assigned_group(student.pk, lesson.group.grade_subject_id)
    attendance, created = Attendance.objects.get_or_create(
        lesson=lesson,
        student=student,
        defaults={
            "assigned_group_id": assigned_group_id,
            "attended_group_id": lesson.group_id,
            "state": AttendanceState.CHECKED_IN,
            "status": (
                AttendanceStatus.LATE if now > lesson.late_after else AttendanceStatus.PRESENT
            ),
            "attendance_type": attendance_type,
            "check_in_at": now,
            "late_minutes": lesson.late_minutes_for(now),
            "is_manual": True,
            "checked_in_by": actor,
            "scan_source": ScanSource.MANUAL,
            "notes": reason,
        },
    )
    if not created:
        raise DomainError("ERR_ALREADY_RECORDED", _("للطالب تسجيل بالفعل في هذه الحصة"), status=409)

    record(AuditAction.ATTENDANCE_CREATED, attendance, reason=reason, actor=actor)
    AttendanceEvent.objects.create(
        attendance=attendance,
        lesson=lesson,
        student=student,
        event_type=AttendanceEventType.MANUAL_EDIT,
        result_code="MANUAL_CREATE",
        message=reason[:200],
        operator=actor,
    )
    return attendance


@transaction.atomic
def cancel_attendance(attendance: Attendance, *, actor, reason: str) -> Attendance:
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("سبب الإلغاء مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    previous = attendance.state
    attendance.state = AttendanceState.CANCELLED
    attendance.status = AttendanceStatus.CANCELLED
    attendance.is_manual = True
    attendance.save(update_fields=["state", "status", "is_manual", "updated_at"])

    record(
        AuditAction.ATTENDANCE_CANCELLED,
        attendance,
        changes={"state": {"old": previous, "new": AttendanceState.CANCELLED}},
        reason=reason,
        actor=actor,
    )
    return attendance


# --------------------------------------------------------------------------- #
# Lesson completion sweep (TASK-055)
# --------------------------------------------------------------------------- #

@transaction.atomic
def finalize_lesson(lesson, *, actor=None) -> dict:
    """Auto-close dangling check-ins and materialise absences.

    Idempotent: re-completing a lesson creates nothing new. Students who
    attended this subject in *another* group are still absent from their own
    lesson — that is correct, and the alternative-group report explains why.
    """
    now = lesson.actual_end_at or timezone.now()
    auto_checkout = settings_registry.get("attendance.auto_checkout_at_lesson_end")
    materialize = settings_registry.get("attendance.materialize_absences")
    partial_minutes = settings_registry.get("attendance.partial_min_duration_minutes")

    checked_out = 0
    if auto_checkout:
        dangling = Attendance.objects.select_for_update().filter(
            lesson=lesson, state=AttendanceState.CHECKED_IN
        )
        for attendance in dangling:
            end = max(now, attendance.check_in_at)
            attendance.check_out_at = end
            attendance.state = AttendanceState.CHECKED_OUT
            attendance.duration_minutes = int(
                (end - attendance.check_in_at).total_seconds() // 60
            )
            if attendance.duration_minutes < partial_minutes:
                attendance.status = AttendanceStatus.PARTIAL
            attendance.scan_source = ScanSource.SYSTEM
            attendance.notes = f"{attendance.notes}\nauto-checkout".strip()
            attendance.save(
                update_fields=[
                    "check_out_at", "state", "duration_minutes", "status",
                    "scan_source", "notes", "updated_at",
                ]
            )
            checked_out += 1

    absences = 0
    if materialize:
        from apps.students.models import AssignmentStatus, StudentGroupAssignment

        already = set(
            Attendance.objects.filter(lesson=lesson).values_list("student_id", flat=True)
        )
        expected = StudentGroupAssignment.objects.filter(
            group=lesson.group,
            status=AssignmentStatus.ACTIVE,
            start_date__lte=lesson.lesson_date,
        ).values_list("student_id", flat=True)

        rows = [
            Attendance(
                lesson=lesson,
                student_id=student_id,
                assigned_group_id=lesson.group_id,
                attended_group_id=lesson.group_id,
                state=AttendanceState.ABSENT,
                status=AttendanceStatus.ABSENT,
                attendance_type=AttendanceType.NORMAL,
                scan_source=ScanSource.SYSTEM,
            )
            for student_id in expected
            if student_id not in already
        ]
        if rows:
            Attendance.objects.bulk_create(rows, ignore_conflicts=True)
            absences = len(rows)

    return {"auto_checked_out": checked_out, "absences": absences}
