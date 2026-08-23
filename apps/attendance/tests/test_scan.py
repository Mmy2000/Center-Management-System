"""The mandatory scan cases from docs/06 §K.2 (1–18)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.attendance import services
from apps.attendance.models import (
    Attendance,
    AttendanceEvent,
    AttendanceState,
    AttendanceStatus,
    AttendanceType,
)
from apps.attendance.services import ScanRejected
from apps.cards.models import CardStatus, StudentCard
from apps.cards.services import replace_card
from apps.core.registry import settings_registry
from apps.lessons.services import cancel_lesson, complete_lesson
from apps.students import assignment_services as assign_svc
from apps.students.models import StudentGroupAssignment, StudentStatus
from apps.students.services import change_status, create_student

from .conftest import make_lesson

pytestmark = pytest.mark.django_db


def scan(lesson, token, **kwargs):
    return services.scan(lesson_id=lesson.pk, qr_token=token, **kwargs)


# --------------------------------------------------------------- 1, 2, 3, 4 #


def test_1_normal_check_in(assigned_student, lesson_a):
    student, card = assigned_student
    result = scan(lesson_a, card.qr_token)

    assert result["code"] == "OK_CHECK_IN"
    attendance = Attendance.objects.get()
    assert attendance.state == AttendanceState.CHECKED_IN
    assert attendance.status == AttendanceStatus.PRESENT
    assert attendance.attendance_type == AttendanceType.NORMAL
    assert attendance.assigned_group_id == attendance.attended_group_id
    assert attendance.card_used_id == card.pk


def test_2_late_check_in_freezes_the_delay(assigned_student, world):
    student, card = assigned_student
    started = timezone.now() - timedelta(minutes=25)
    lesson = make_lesson(world["group_a"], start=started)

    result = scan(lesson, card.qr_token)
    attendance = Attendance.objects.get()

    assert result["code"] == "OK_CHECK_IN_LATE"
    assert attendance.status == AttendanceStatus.LATE
    assert 24 <= attendance.late_minutes <= 26


def test_3_alternative_group_never_touches_the_assignment(assigned_student, world, lesson_b):
    """The Principle-2 regression test."""
    student, card = assigned_student
    before = list(
        StudentGroupAssignment.objects.filter(student=student).values("id", "group_id", "status")
    )

    result = scan(lesson_b, card.qr_token)
    attendance = Attendance.objects.get()

    assert result["code"] == "OK_CHECK_IN_ALTERNATIVE"
    assert attendance.attendance_type == AttendanceType.ALTERNATIVE_GROUP
    assert attendance.assigned_group_id == world["group_a"].pk
    assert attendance.attended_group_id == world["group_b"].pk
    assert attendance.assigned_group_id != attendance.attended_group_id

    after = list(
        StudentGroupAssignment.objects.filter(student=student).values("id", "group_id", "status")
    )
    assert before == after


def test_4_makeup_links_the_missed_lesson(assigned_student, world, lesson_b):
    student, card = assigned_student
    missed = make_lesson(world["group_a"], start=timezone.now() - timedelta(days=2), open_it=False)

    result = scan(lesson_b, card.qr_token, makeup_for_lesson_id=missed.pk)
    attendance = Attendance.objects.get(lesson=lesson_b)

    assert result["code"] == "OK_CHECK_IN_MAKEUP"
    assert attendance.attendance_type == AttendanceType.MAKEUP
    assert attendance.makeup_for_lesson_id == missed.pk


# ------------------------------------------------------------------ 5, 6, 14 #


@pytest.mark.parametrize(
    "policy,expected",
    [
        ("BLOCK", "ERR_NOT_ASSIGNED_BLOCKED"),
        ("WARN", "WARN_NOT_ASSIGNED"),
        ("REQUIRE_APPROVAL", "NEEDS_APPROVAL_NOT_ASSIGNED"),
    ],
)
def test_5_not_assigned_follows_the_policy(carded_student, world, policy, expected):
    student, card = carded_student  # enrolled in nothing
    lesson = make_lesson(world["group_a"])
    settings_registry.set("attendance.not_assigned_policy", policy)

    if policy == "WARN":
        result = scan(lesson, card.qr_token)
        assert expected in result["warnings"] or result["code"] == "WARN_NOT_ASSIGNED"
        assert Attendance.objects.count() == 1
    else:
        with pytest.raises(ScanRejected) as exc:
            scan(lesson, card.qr_token)
        assert exc.value.code == expected
        assert Attendance.objects.count() == 0


def test_6_different_grade_requires_approval(carded_student, world):
    student, card = carded_student
    lesson = make_lesson(world["sec2_group"])  # a 2nd-secondary lesson

    with pytest.raises(ScanRejected) as exc:
        scan(lesson, card.qr_token)
    assert exc.value.code == "NEEDS_APPROVAL_GRADE"

    result = scan(lesson, card.qr_token, approve=True)
    assert result["attendance"]["type"] == AttendanceType.EXCEPTIONAL
    assert Attendance.objects.get().requires_approval is True


def test_14_outside_the_window(assigned_student, world):
    student, card = assigned_student
    lesson = make_lesson(world["group_a"], start=timezone.now() - timedelta(hours=6))

    settings_registry.set("attendance.outside_window_policy", "BLOCK")
    with pytest.raises(ScanRejected) as exc:
        scan(lesson, card.qr_token)
    assert exc.value.code == "ERR_WINDOW_CLOSED"

    settings_registry.set("attendance.outside_window_policy", "ALLOW_EXCEPTIONAL")
    result = scan(lesson, card.qr_token)
    assert "WARN_OUTSIDE_WINDOW" in result["warnings"]
    assert Attendance.objects.get().attendance_type == AttendanceType.EXCEPTIONAL


# ----------------------------------------------------------- 7, 8, 9, 10, 18 #


def test_7_duplicate_scan_inside_the_window_writes_nothing(assigned_student, lesson_a):
    student, card = assigned_student
    first = scan(lesson_a, card.qr_token)
    second = scan(lesson_a, card.qr_token)

    assert second["code"] == "WARN_DUPLICATE"
    assert second["attendance"]["check_in"] == first["attendance"]["check_in"]
    assert Attendance.objects.count() == 1
    assert Attendance.objects.get().state == AttendanceState.CHECKED_IN


def test_8_second_scan_after_the_gap_is_a_check_out(assigned_student, lesson_a):
    student, card = assigned_student
    scan(lesson_a, card.qr_token)

    attendance = Attendance.objects.get()
    attendance.check_in_at = timezone.now() - timedelta(minutes=90)
    attendance.save(update_fields=["check_in_at"])

    result = scan(lesson_a, card.qr_token)
    attendance.refresh_from_db()

    assert result["code"] == "OK_CHECK_OUT"
    assert attendance.state == AttendanceState.CHECKED_OUT
    assert 89 <= attendance.duration_minutes <= 91


def test_18_short_stay_becomes_partial(assigned_student, lesson_a):
    student, card = assigned_student
    scan(lesson_a, card.qr_token)

    attendance = Attendance.objects.get()
    attendance.check_in_at = timezone.now() - timedelta(minutes=15)
    attendance.save(update_fields=["check_in_at"])

    result = scan(lesson_a, card.qr_token)
    attendance.refresh_from_db()
    assert result["code"] == "OK_CHECK_OUT_PARTIAL"
    assert attendance.status == AttendanceStatus.PARTIAL


def test_9_check_out_without_check_in(assigned_student, lesson_a):
    student, card = assigned_student
    with pytest.raises(ScanRejected) as exc:
        scan(lesson_a, card.qr_token, intent="CHECK_OUT")
    assert exc.value.code == "ERR_NOT_CHECKED_IN"


def test_10_scan_after_check_out_warns(assigned_student, lesson_a):
    student, card = assigned_student
    scan(lesson_a, card.qr_token)
    attendance = Attendance.objects.get()
    attendance.check_in_at = timezone.now() - timedelta(minutes=90)
    attendance.save(update_fields=["check_in_at"])
    scan(lesson_a, card.qr_token)  # check-out

    result = scan(lesson_a, card.qr_token)
    assert result["code"] == "WARN_ALREADY_CHECKED_OUT"
    assert Attendance.objects.get().state == AttendanceState.CHECKED_OUT


# ------------------------------------------------------------- 11, 12, 13, 3 #


@pytest.mark.parametrize(
    "status,expected",
    [
        (CardStatus.LOST, "ERR_CARD_LOST"),
        (CardStatus.DISABLED, "ERR_CARD_DISABLED"),
        (CardStatus.REPLACED, "ERR_CARD_REPLACED"),
        (CardStatus.AVAILABLE, "ERR_CARD_UNASSIGNED"),
    ],
)
def test_11_card_states_have_distinct_codes(world, lesson_a, status, expected):
    card = StudentCard.objects.create(card_number="CARD-X", qr_token="CMS1:xxxxxxxxxxxx")
    card.status = status
    card.save()

    with pytest.raises(ScanRejected) as exc:
        scan(lesson_a, card.qr_token)
    assert exc.value.code == expected


def test_unknown_token_is_rejected_without_a_row(lesson_a):
    from apps.core.http import DomainError

    with pytest.raises(DomainError) as exc:
        scan(lesson_a, "CMS1:doesnotexist")
    assert exc.value.code == "ERR_CARD_NOT_FOUND"
    assert Attendance.objects.count() == 0


def test_12_replacement_keeps_history_and_swaps_which_token_works(
    assigned_student, world, lesson_a
):
    student, old_card = assigned_student
    scan(lesson_a, old_card.qr_token)
    history_before = list(Attendance.objects.values("id", "student_id", "card_used_id"))

    new_card = StudentCard.objects.create(
        card_number="CARD-000002", qr_token="CMS1:bbbbbbbbbbbbbbbbbbbbbb"
    )
    replace_card(old_card, new_card, reason="فقد", old_status=CardStatus.LOST)

    # A second lesson starting shortly — inside its check-in window right now.
    later = make_lesson(world["group_a"], start=timezone.now() + timedelta(minutes=5))
    with pytest.raises(ScanRejected) as exc:
        scan(later, old_card.qr_token)
    assert exc.value.code == "ERR_CARD_LOST"

    result = scan(later, new_card.qr_token)
    assert result["code"] == "OK_CHECK_IN"

    # The original attendance row is untouched — it points at the student.
    assert history_before == list(
        Attendance.objects.filter(lesson=lesson_a).values("id", "student_id", "card_used_id")
    )


def test_13_lesson_states(assigned_student, world):
    student, card = assigned_student

    scheduled = make_lesson(world["group_a"], open_it=False)
    with pytest.raises(ScanRejected) as exc:
        scan(scheduled, card.qr_token)
    assert exc.value.code == "ERR_LESSON_NOT_OPEN"

    cancelled = make_lesson(world["group_a"], start=timezone.now() + timedelta(hours=2))
    cancel_lesson(cancelled, reason="غياب المدرس")
    with pytest.raises(ScanRejected) as exc:
        scan(cancelled, card.qr_token)
    assert exc.value.code == "ERR_LESSON_CANCELLED"


def test_suspended_student_is_refused(assigned_student, lesson_a):
    student, card = assigned_student
    change_status(student, StudentStatus.SUSPENDED, reason="مخالفة")

    with pytest.raises(ScanRejected) as exc:
        scan(lesson_a, card.qr_token)
    assert exc.value.code == "ERR_STUDENT_SUSPENDED"
    assert Attendance.objects.count() == 0


# --------------------------------------------------------------- 16, events #


def test_16_replaying_an_idempotency_key_returns_the_first_answer(assigned_student, lesson_a):
    student, card = assigned_student
    first = scan(lesson_a, card.qr_token, idempotency_key="key-1")
    second = scan(lesson_a, card.qr_token, idempotency_key="key-1")

    assert second["replayed"] is True
    assert second["code"] == first["code"]
    assert Attendance.objects.count() == 1
    assert AttendanceEvent.objects.filter(idempotency_key="key-1").count() == 1


def test_refusals_are_logged_as_events_but_do_not_consume_the_key(carded_student, world):
    student, card = carded_student
    lesson = make_lesson(world["group_a"])
    from apps.core.registry import settings_registry as registry

    registry.set("attendance.not_assigned_policy", "BLOCK")

    with pytest.raises(ScanRejected):
        scan(lesson, card.qr_token, idempotency_key="key-2")

    event = AttendanceEvent.objects.get()
    assert event.event_type == "DENIED"
    assert event.result_code == "ERR_NOT_ASSIGNED_BLOCKED"
    assert event.idempotency_key is None
    assert event.attendance_id is None


def test_37_the_raw_token_never_reaches_the_event_log(assigned_student, lesson_a):
    student, card = assigned_student
    scan(lesson_a, card.qr_token, device_id="desk-1")

    event = AttendanceEvent.objects.get()
    assert card.qr_token not in str(event.payload)
    assert card.qr_token not in event.message
    assert event.card_id == card.pk


# ------------------------------------------------------------------- sweeps #


def test_17_completion_auto_checks_out_and_materialises_absences(world, assigned_student):
    present, card = assigned_student
    absent = create_student(
        full_name="سارة علي", grade=world["grade"], guardian_phone="01112345678"
    )
    assign_svc.assign_student(absent, world["group_a"])

    lesson = make_lesson(world["group_a"])
    scan(lesson, card.qr_token)

    lesson, summary = complete_lesson(lesson)
    assert summary["auto_checked_out"] == 1
    assert summary["absences"] == 1

    present_row = Attendance.objects.get(student=present)
    absent_row = Attendance.objects.get(student=absent)
    assert present_row.state == AttendanceState.CHECKED_OUT
    assert "auto-checkout" in present_row.notes
    assert absent_row.state == AttendanceState.ABSENT
    assert absent_row.status == AttendanceStatus.ABSENT

    # Idempotent.
    lesson.refresh_from_db()
    again = services.finalize_lesson(lesson)
    assert again == {"auto_checked_out": 0, "absences": 0}
    assert Attendance.objects.count() == 2


def test_alternative_attendee_is_still_absent_from_their_own_lesson(world, assigned_student):
    """Correct, and exactly what the alternative-group report explains."""
    student, card = assigned_student
    own_lesson = make_lesson(world["group_a"])
    other_lesson = make_lesson(world["group_b"])

    scan(other_lesson, card.qr_token)
    complete_lesson(own_lesson)

    own_row = Attendance.objects.get(lesson=own_lesson, student=student)
    other_row = Attendance.objects.get(lesson=other_lesson, student=student)
    assert own_row.state == AttendanceState.ABSENT
    assert other_row.attendance_type == AttendanceType.ALTERNATIVE_GROUP


def test_absence_is_corrected_by_a_later_scan(world, assigned_student):
    student, card = assigned_student
    lesson = make_lesson(world["group_a"])
    services.finalize_lesson(lesson)
    assert Attendance.objects.get().state == AttendanceState.ABSENT

    result = scan(lesson, card.qr_token)
    assert result["code"] == "OK_CHECK_IN"
    assert Attendance.objects.get().state == AttendanceState.CHECKED_IN
    assert Attendance.objects.count() == 1


def test_students_assigned_after_the_lesson_are_not_retroactively_absent(world, carded_student):
    from datetime import timedelta as td

    student, card = carded_student
    lesson = make_lesson(world["group_a"], start=timezone.now() - td(days=3))
    assign_svc.assign_student(student, world["group_a"])  # starts today

    summary = services.finalize_lesson(lesson)
    assert summary["absences"] == 0


# ------------------------------------------------------------ query budget #


def _data_queries(captured):
    """Only real statements — BEGIN/COMMIT/SAVEPOINT are transaction control."""
    noise = ("BEGIN", "COMMIT", "SAVEPOINT", "RELEASE", "ROLLBACK")
    return [q for q in captured if not q["sql"].upper().startswith(noise)]


def test_scan_stays_within_the_query_budget(assigned_student, lesson_a):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.lessons.services import lesson_snapshot

    student, card = assigned_student
    lesson_snapshot(lesson_a.pk)  # warm the cache as it is in production

    with CaptureQueriesContext(connection) as captured:
        services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)

    # card lookup · assignment lookup · attendance select+insert · event insert
    queries = _data_queries(captured.captured_queries)
    assert len(queries) <= 5, "\n".join(q["sql"][:120] for q in queries)
    # The usual group's *name* must come from the assignment join, never from a
    # separate SELECT on academics_group.
    standalone_group_selects = [
        q for q in queries if q["sql"].upper().startswith('SELECT "ACADEMICS_GROUP"')
    ]
    assert standalone_group_selects == []


def test_payment_gate_costs_nothing_while_disabled(assigned_student, lesson_a):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    student, card = assigned_student
    assert settings_registry.get("payments.enforce_on_attendance") is False

    with CaptureQueriesContext(connection) as captured:
        services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)
    assert not any("monthlycharge" in q["sql"].lower() for q in captured.captured_queries)
