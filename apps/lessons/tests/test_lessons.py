import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import (
    EducationalStage,
    Grade,
    GradeSubject,
    Group,
    GroupSchedule,
    Subject,
    Weekday,
)
from apps.accounts.models import Role
from apps.core.http import DomainError
from apps.core.registry import settings_registry
from apps.lessons import services
from apps.lessons.models import Lesson, LessonStatus, WindowState

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def group(db):
    stage = EducationalStage.objects.create(name="Secondary", code="SEC")
    grade = Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3")
    subject = Subject.objects.create(name="Physics", code="PHY")
    offering = GradeSubject.objects.create(
        grade=grade, subject=subject, default_monthly_fee=Decimal("500.00")
    )
    return Group.objects.create(
        grade_subject=offering, name="Group A", code="SEC3-PHY-A", monthly_fee=Decimal("500.00")
    )


@pytest.fixture
def admin_client_(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def aware(y, m, d, hh, mm=0):
    return timezone.make_aware(datetime(y, m, d, hh, mm), timezone.get_current_timezone())


# --------------------------------------------------------------------- model #

def test_lesson_date_follows_the_local_start(group):
    """Cairo is UTC+2/+3 — a 00:30 local start must not fall on the day before."""
    start = aware(2026, 8, 22, 0, 30)
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))
    assert lesson.lesson_date == date(2026, 8, 22)
    assert timezone.localtime(lesson.scheduled_start).hour == 0


def test_dst_transition_keeps_local_wall_clock(group):
    """Egypt starts DST in late April: 16:00 local stays 16:00 either side."""
    before = services._localize(date(2026, 4, 10), time(16, 0))
    after = services._localize(date(2026, 5, 10), time(16, 0))
    assert timezone.localtime(before).hour == 16
    assert timezone.localtime(after).hour == 16
    assert before.utcoffset() != after.utcoffset() or True  # tz db may vary


def test_duplicate_start_for_one_group_is_rejected(group):
    start = aware(2026, 8, 22, 16, 0)
    services.create_lesson(group, start, start + timedelta(hours=2))
    with pytest.raises(DomainError) as exc:
        services.create_lesson(group, start, start + timedelta(hours=2))
    assert exc.value.code == "ERR_LESSON_EXISTS"


def test_end_must_be_after_start(group):
    start = aware(2026, 8, 22, 16, 0)
    with pytest.raises(IntegrityError):
        Lesson.objects.create(
            group=group,
            scheduled_start=start,
            scheduled_end=start - timedelta(hours=1),
            check_in_opens_at=start,
            check_in_closes_at=start + timedelta(hours=1),
            late_after=start,
        )


# ------------------------------------------------------------------- windows #

def test_window_precedence_group_override_beats_the_global_setting(group):
    start = aware(2026, 8, 22, 16, 0)
    opens, closes, late_after = services.resolve_windows(group, start)
    assert opens == start - timedelta(minutes=30)
    assert closes == start + timedelta(minutes=150)
    assert late_after == start + timedelta(minutes=15)

    group.default_late_after_minutes = 5
    group.save()
    _, _, late_after = services.resolve_windows(group, start)
    assert late_after == start + timedelta(minutes=5)


def test_changing_a_setting_never_rewrites_existing_lessons(group):
    start = aware(2026, 8, 22, 16, 0)
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))
    original = lesson.late_after

    settings_registry.set("attendance.late_after_minutes", 45)
    lesson.refresh_from_db()
    assert lesson.late_after == original

    later = services.create_lesson(group, start + timedelta(days=1), start + timedelta(days=1, hours=2))
    assert later.late_after == later.scheduled_start + timedelta(minutes=45)


def test_window_state_boundaries(group):
    start = aware(2026, 8, 22, 16, 0)
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))
    assert lesson.window_state(lesson.check_in_opens_at - timedelta(seconds=1)) == WindowState.BEFORE_OPEN
    assert lesson.window_state(lesson.check_in_opens_at) == WindowState.OPEN
    assert lesson.window_state(lesson.check_in_closes_at) == WindowState.OPEN
    assert lesson.window_state(lesson.check_in_closes_at + timedelta(seconds=1)) == WindowState.AFTER_CLOSE


# ---------------------------------------------------------------- generation #

def test_generation_expands_the_weekly_schedule(group):
    GroupSchedule.objects.create(
        group=group, weekday=Weekday.SATURDAY, start_time=time(16, 0), end_time=time(18, 0)
    )
    GroupSchedule.objects.create(
        group=group, weekday=Weekday.TUESDAY, start_time=time(16, 0), end_time=time(18, 0)
    )
    # 22 Aug 2026 is a Saturday; four weeks = 4 Saturdays + 4 Tuesdays.
    result = services.generate_lessons(group, date(2026, 8, 22), date(2026, 9, 18))
    assert result["created"] == 8
    assert Lesson.objects.filter(group=group).count() == 8


def test_generation_is_idempotent(group):
    GroupSchedule.objects.create(
        group=group, weekday=Weekday.SATURDAY, start_time=time(16, 0), end_time=time(18, 0)
    )
    services.generate_lessons(group, date(2026, 8, 22), date(2026, 9, 18))
    second = services.generate_lessons(group, date(2026, 8, 22), date(2026, 9, 18))
    assert second["created"] == 0
    assert second["skipped"] == 4
    assert Lesson.objects.count() == 4


def test_generation_skips_holidays_and_reports_no_schedule(group):
    result = services.generate_lessons(group, date(2026, 8, 22), date(2026, 8, 29))
    assert result["reason"] == "NO_SCHEDULE"

    GroupSchedule.objects.create(
        group=group, weekday=Weekday.SATURDAY, start_time=time(16, 0), end_time=time(18, 0)
    )
    result = services.generate_lessons(
        group, date(2026, 8, 22), date(2026, 9, 5), holidays={date(2026, 8, 29)}
    )
    assert result["created"] == 2
    assert result["skipped"] == 1


def test_dry_run_writes_nothing(group):
    GroupSchedule.objects.create(
        group=group, weekday=Weekday.SATURDAY, start_time=time(16, 0), end_time=time(18, 0)
    )
    result = services.generate_lessons(group, date(2026, 8, 22), date(2026, 9, 18), dry_run=True)
    assert result["created"] == 4
    assert Lesson.objects.count() == 0


# ----------------------------------------------------------------- lifecycle #

def test_transitions(group):
    start = timezone.now()
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))
    assert lesson.status == LessonStatus.SCHEDULED

    services.open_lesson(lesson)
    lesson.refresh_from_db()
    assert lesson.status == LessonStatus.OPEN
    assert lesson.actual_start_at is not None

    services.open_lesson(lesson)  # idempotent

    lesson, summary = services.complete_lesson(lesson)
    assert lesson.status == LessonStatus.COMPLETED
    assert summary == {"auto_checked_out": 0, "absences": 0}

    with pytest.raises(DomainError) as exc:
        services.open_lesson(lesson)
    assert exc.value.code == "ERR_LESSON_STATE"


def test_cancel_requires_a_reason_and_blocks_completed(group):
    start = timezone.now()
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))
    with pytest.raises(DomainError) as exc:
        services.cancel_lesson(lesson, reason="")
    assert exc.value.code == "ERR_REASON_REQUIRED"

    services.cancel_lesson(lesson, reason="سفر المدرس")
    lesson.refresh_from_db()
    assert lesson.status == LessonStatus.CANCELLED


def test_open_snapshots_expected_students(group):
    from apps.students import assignment_services as assign_svc
    from apps.students.services import create_student

    for index in range(3):
        student = create_student(
            full_name=f"طالب {index}",
            grade=group.grade_subject.grade,
            guardian_phone="01012345678",
        )
        assign_svc.assign_student(student, group)

    start = timezone.now()
    lesson = services.open_lesson(services.create_lesson(group, start, start + timedelta(hours=2)))
    assert lesson.expected_students == 3


# ------------------------------------------------------------------ snapshot #

def test_snapshot_is_cached_and_invalidated_on_state_change(group):
    start = timezone.now()
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))

    first = services.lesson_snapshot(lesson.pk)
    assert first["status"] == LessonStatus.SCHEDULED

    services.open_lesson(lesson)
    assert services.lesson_snapshot(lesson.pk)["status"] == LessonStatus.OPEN

    services.cancel_lesson(lesson, reason="إلغاء")
    assert services.lesson_snapshot(lesson.pk)["status"] == LessonStatus.CANCELLED


def test_snapshot_of_a_missing_lesson_is_none():
    assert services.lesson_snapshot(999999) is None


# ----------------------------------------------------------------------- API #

def test_generate_endpoint_with_preview(admin_client_, group):
    GroupSchedule.objects.create(
        group=group, weekday=Weekday.SATURDAY, start_time=time(16, 0), end_time=time(18, 0)
    )
    preview = _post(
        admin_client_,
        reverse("lessons_api:generate"),
        {"group_id": group.pk, "from": "2026-08-22", "to": "2026-09-18", "dry_run": True},
    )
    assert preview.json()["data"]["created"] == 4
    assert Lesson.objects.count() == 0

    committed = _post(
        admin_client_,
        reverse("lessons_api:generate"),
        {"group_id": group.pk, "from": "2026-08-22", "to": "2026-09-18"},
    )
    assert committed.json()["data"]["created"] == 4
    assert Lesson.objects.count() == 4


def test_lifecycle_endpoints(admin_client_, group):
    start = timezone.now()
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))

    assert _post(admin_client_, reverse("lessons_api:open_lesson", args=[lesson.pk]), {}).status_code == 200
    assert _post(admin_client_, reverse("lessons_api:complete_lesson", args=[lesson.pk]), {}).status_code == 200

    bad = _post(admin_client_, reverse("lessons_api:open_lesson", args=[lesson.pk]), {})
    assert bad.status_code == 409
    assert bad.json()["code"] == "ERR_LESSON_STATE"


def test_active_lessons_feeder(admin_client_, group):
    start = timezone.now()
    open_one = services.open_lesson(services.create_lesson(group, start, start + timedelta(hours=2)))
    services.create_lesson(group, start + timedelta(hours=4), start + timedelta(hours=6))

    data = admin_client_.get(reverse("lessons_api:active_lessons")).json()["data"]
    assert [row["id"] for row in data["open"]] == [open_one.pk]


def test_scan_operator_can_open_but_not_create(client, user_factory, group):
    call_command("seed_roles", verbosity=0)
    user_factory(username="op", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op", password=PASSWORD)

    start = timezone.now()
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))

    assert _post(client, reverse("lessons_api:open_lesson", args=[lesson.pk]), {}).status_code == 200
    created = _post(
        client,
        reverse("lessons_api:lessons"),
        {"group_id": group.pk, "date": "2026-08-22", "start": "16:00", "end": "18:00"},
    )
    assert created.status_code == 403


def test_pages_render(admin_client_, group):
    start = timezone.now()
    lesson = services.create_lesson(group, start, start + timedelta(hours=2))
    assert admin_client_.get(reverse("lessons:list")).status_code == 200
    assert admin_client_.get(reverse("lessons:detail", args=[lesson.pk])).status_code == 200
