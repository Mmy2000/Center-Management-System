"""Scan endpoint contract, permissions, manual corrections (TASK-053/054)."""

import json

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import Instructor
from apps.accounts.models import Role
from apps.attendance import services
from apps.attendance.models import Attendance, AttendanceState, AttendanceStatus, AttendanceType
from apps.attendance.result_codes import CODES, PRESENTATION, info
from apps.core.models import AuditAction, AuditLog
from apps.core.registry import settings_registry

from .conftest import PASSWORD, make_lesson

pytestmark = pytest.mark.django_db


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _patch(client, url, payload):
    return client.patch(url, data=json.dumps(payload), content_type="application/json")


# ---------------------------------------------------------- result codes (48) #


def test_every_code_has_a_complete_presentation_mapping():
    for code in CODES:
        row = info(code)
        assert row["message"], code
        assert row["message_en"], code
        assert row["colour"] in {"green", "amber", "red"}
        assert row["sound"] in {"beep", "double-beep", "buzz"}
        assert row["severity"] in PRESENTATION


def test_unknown_code_fails_loudly():
    with pytest.raises(KeyError):
        info("OK_MADE_UP")


# ------------------------------------------------------------- scan endpoint #


def test_scan_contract_matches_the_documented_payload(admin_client_, assigned_student, lesson_a):
    student, card = assigned_student
    response = _post(
        admin_client_,
        reverse("attendance_api:scan"),
        {
            "lesson_id": lesson_a.pk,
            "qr_token": card.qr_token,
            "device_id": "desk-1",
            "scan_source": "HID",
            "idempotency_key": "abc-1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["code"] == "OK_CHECK_IN"

    data = body["data"]
    assert set(data["student"]) == {"id", "code", "name", "photo"}
    assert set(data["groups"]) == {"assigned", "attended"}
    assert data["attendance"]["state"] == "CHECKED_IN"
    assert isinstance(data["latency_ms"], int)


def test_scan_response_never_leaks_pii_or_the_token(admin_client_, assigned_student, lesson_a):
    student, card = assigned_student
    student.guardian_phone = "01099998888"
    student.address = "شارع الهرم"
    student.save()

    response = _post(
        admin_client_,
        reverse("attendance_api:scan"),
        {"lesson_id": lesson_a.pk, "qr_token": card.qr_token},
    )
    text = response.content.decode("utf-8")
    assert card.qr_token not in text
    assert "01099998888" not in text
    assert "الهرم" not in text


def test_oversized_token_is_rejected_without_touching_the_database(
    admin_client_, lesson_a, django_assert_max_num_queries
):
    response = _post(
        admin_client_,
        reverse("attendance_api:scan"),
        {"lesson_id": lesson_a.pk, "qr_token": "x" * 200},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "ERR_CARD_NOT_FOUND"
    assert Attendance.objects.count() == 0


def test_missing_lesson_id_is_a_field_error(admin_client_):
    response = _post(admin_client_, reverse("attendance_api:scan"), {"qr_token": "CMS1:x"})
    assert response.status_code == 400
    assert "lesson_id" in response.json()["field_errors"]


def test_approval_needed_is_not_an_http_error(admin_client_, carded_student, world):
    student, card = carded_student
    lesson = make_lesson(world["group_a"])
    settings_registry.set("attendance.not_assigned_policy", "REQUIRE_APPROVAL")

    response = _post(
        admin_client_,
        reverse("attendance_api:scan"),
        {"lesson_id": lesson.pk, "qr_token": card.qr_token},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "NEEDS_APPROVAL_NOT_ASSIGNED"
    assert body["data"]["requires_approval"] is True
    assert Attendance.objects.count() == 0

    approved = _post(
        admin_client_,
        reverse("attendance_api:scan"),
        {"lesson_id": lesson.pk, "qr_token": card.qr_token, "approve": True},
    )
    assert approved.status_code == 200
    assert Attendance.objects.get().attendance_type == AttendanceType.EXCEPTIONAL


def test_operator_without_approval_permission_cannot_force(
    client, user_factory, carded_student, world
):
    call_command("seed_roles", verbosity=0)
    user_factory(username="op2", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op2", password=PASSWORD)

    student, card = carded_student
    lesson = make_lesson(world["group_a"])
    response = _post(
        client,
        reverse("attendance_api:scan"),
        {"lesson_id": lesson.pk, "qr_token": card.qr_token, "approve": True},
    )
    assert response.status_code == 403


def test_instructor_cannot_scan_another_instructors_lesson(
    client, user_factory, world, assigned_student
):
    call_command("seed_roles", verbosity=0)
    teacher = user_factory(username="teach", role=Role.INSTRUCTOR, password=PASSWORD)
    Instructor.objects.create(full_name="أ. محمد", user=teacher)
    theirs = Instructor.objects.create(full_name="أ. هدى")
    world["group_a"].instructor = theirs
    world["group_a"].save()

    student, card = assigned_student
    lesson = make_lesson(world["group_a"])
    client.login(username="teach", password=PASSWORD)

    response = _post(
        client,
        reverse("attendance_api:scan"),
        {"lesson_id": lesson.pk, "qr_token": card.qr_token},
    )
    assert response.status_code == 404  # existence is not disclosed


def test_payment_block_is_hidden_from_roles_without_the_permission(
    client, user_factory, assigned_student, lesson_a
):
    call_command("seed_roles", verbosity=0)
    user_factory(username="op3", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op3", password=PASSWORD)

    student, card = assigned_student
    response = _post(
        client,
        reverse("attendance_api:scan"),
        {"lesson_id": lesson_a.pk, "qr_token": card.qr_token},
    )
    assert response.status_code == 200
    assert "payment" not in response.json()["data"]


# ----------------------------------------------------- manual corrections (54) #


def test_manual_attendance_requires_a_reason(admin_client_, assigned_student, lesson_a):
    student, card = assigned_student
    response = _post(
        admin_client_,
        reverse("attendance_api:manual"),
        {"lesson_id": lesson_a.pk, "student_id": student.pk},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "ERR_REASON_REQUIRED"

    created = _post(
        admin_client_,
        reverse("attendance_api:manual"),
        {"lesson_id": lesson_a.pk, "student_id": student.pk, "reason": "نسي الكارت"},
    )
    assert created.status_code == 200
    attendance = Attendance.objects.get()
    assert attendance.is_manual is True
    assert attendance.attendance_type == AttendanceType.MANUAL


def test_correction_is_audited_with_before_and_after(admin_client_, assigned_student, lesson_a):
    student, card = assigned_student
    services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)
    attendance = Attendance.objects.get()
    original = attendance.check_in_at

    earlier = (original - timezone.timedelta(minutes=8)).isoformat()
    response = _patch(
        admin_client_,
        reverse("attendance_api:attendance_detail", args=[attendance.pk]),
        {"check_in_at": earlier, "reason": "عطل في القارئ"},
    )
    assert response.status_code == 200

    attendance.refresh_from_db()
    assert attendance.is_manual is True

    entry = AuditLog.objects.get(action=AuditAction.ATTENDANCE_MODIFIED)
    assert entry.reason == "عطل في القارئ"
    assert "check_in_at" in entry.changes


def test_correction_without_a_reason_is_refused(admin_client_, assigned_student, lesson_a):
    student, card = assigned_student
    services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)
    attendance = Attendance.objects.get()

    response = _patch(
        admin_client_,
        reverse("attendance_api:attendance_detail", args=[attendance.pk]),
        {"status": "PRESENT"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "ERR_REASON_REQUIRED"


def test_cancel_marks_the_row_not_deletes_it(admin_client_, assigned_student, lesson_a):
    student, card = assigned_student
    services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)
    attendance = Attendance.objects.get()

    response = _post(
        admin_client_,
        reverse("attendance_api:cancel", args=[attendance.pk]),
        {"reason": "مسح بالخطأ"},
    )
    assert response.status_code == 200
    attendance.refresh_from_db()
    assert attendance.state == AttendanceState.CANCELLED
    assert Attendance.objects.count() == 1


def test_cancelled_attendance_refuses_further_scans(admin_client_, assigned_student, lesson_a):
    student, card = assigned_student
    services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)
    attendance = Attendance.objects.get()
    services.cancel_attendance(attendance, actor=None, reason="خطأ")

    with pytest.raises(services.ScanRejected) as exc:
        services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)
    assert exc.value.code == "ERR_ATTENDANCE_CANCELLED"


def test_scan_operator_cannot_correct_attendance(client, user_factory, assigned_student, lesson_a):
    call_command("seed_roles", verbosity=0)
    student, card = assigned_student
    services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)
    attendance = Attendance.objects.get()

    user_factory(username="op4", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op4", password=PASSWORD)
    response = _patch(
        client,
        reverse("attendance_api:attendance_detail", args=[attendance.pk]),
        {"status": "PRESENT", "reason": "محاولة"},
    )
    assert response.status_code == 403


# ------------------------------------------------------------- dashboards (57) #


def test_lesson_dashboard_counters_reconcile(admin_client_, world, assigned_student):
    from apps.students import assignment_services as assign_svc
    from apps.students.services import create_student

    present, card = assigned_student
    absent = create_student(
        full_name="سارة علي", grade=world["grade"], guardian_phone="01112345678"
    )
    assign_svc.assign_student(absent, world["group_a"])
    lesson = make_lesson(world["group_a"])
    services.scan(lesson_id=lesson.pk, qr_token=card.qr_token)

    data = admin_client_.get(reverse("attendance_api:lesson_attendance", args=[lesson.pk])).json()[
        "data"
    ]

    assert data["counters"]["present"] == 1
    assert data["counters"]["inside"] == 1
    assert data["counters"]["recorded"] == 1
    assert [row["student_id"] for row in data["not_arrived"]] == [absent.pk]

    counted = Attendance.objects.filter(lesson=lesson, status=AttendanceStatus.PRESENT).count()
    assert data["counters"]["present"] == counted


def test_feed_returns_only_new_events(admin_client_, assigned_student, lesson_a):
    student, card = assigned_student
    services.scan(lesson_id=lesson_a.pk, qr_token=card.qr_token)

    first = admin_client_.get(reverse("attendance_api:lesson_feed", args=[lesson_a.pk])).json()[
        "data"
    ]
    assert len(first["results"]) == 1

    from urllib.parse import quote

    again = admin_client_.get(
        reverse("attendance_api:lesson_feed", args=[lesson_a.pk])
        + f"?since={quote(first['cursor'])}"
    ).json()["data"]
    assert again["results"] == []


def test_student_attendance_history_shows_both_groups(admin_client_, world, assigned_student):
    student, card = assigned_student
    other = make_lesson(world["group_b"])
    services.scan(lesson_id=other.pk, qr_token=card.qr_token)

    data = admin_client_.get(
        reverse("attendance_api:student_attendance", args=[student.pk])
    ).json()["data"]
    row = data["results"][0]
    assert row["assigned_group"] == "Group A"
    assert row["attended_group"] == "Group B"
    assert row["type"] == "ALTERNATIVE_GROUP"

    filtered = admin_client_.get(
        reverse("attendance_api:student_attendance", args=[student.pk]) + "?type=NORMAL"
    ).json()["data"]
    assert filtered["count"] == 0


def test_scanner_pages_render(admin_client_, lesson_a):
    assert admin_client_.get(reverse("attendance:scanner_picker")).status_code == 200
    assert admin_client_.get(reverse("attendance:scanner", args=[lesson_a.pk])).status_code == 200
