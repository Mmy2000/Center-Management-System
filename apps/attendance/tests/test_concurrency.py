"""Concurrency guarantees (docs/06 §K.2 case 15, TASK-052).

These need real threads and real row locks, so they run only on PostgreSQL.
On SQLite the correctness still holds through the UNIQUE constraint, which the
single-threaded duplicate tests already cover.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, connections
from django.test import TransactionTestCase

from apps.attendance import services
from apps.attendance.models import Attendance

pytestmark = pytest.mark.postgres


@pytest.mark.skipif(
    connection.vendor == "sqlite", reason="threaded row locking requires PostgreSQL"
)
class SimultaneousScanTests(TransactionTestCase):
    """Two scanners, same card, same lesson, same instant → exactly one row."""

    reset_sequences = True

    def setUp(self):
        from datetime import timedelta
        from decimal import Decimal

        from django.utils import timezone

        from apps.academics.models import EducationalStage, Grade, GradeSubject, Group, Subject
        from apps.cards.models import StudentCard
        from apps.cards.services import assign_card
        from apps.lessons.services import create_lesson, open_lesson
        from apps.students import assignment_services as assign_svc
        from apps.students.services import create_student

        stage = EducationalStage.objects.create(name="Secondary", code="SEC")
        grade = Grade.objects.create(stage=stage, name="G3", code="SEC3")
        subject = Subject.objects.create(name="Physics", code="PHY")
        offering = GradeSubject.objects.create(
            grade=grade, subject=subject, default_monthly_fee=Decimal("500.00")
        )
        self.group = Group.objects.create(
            grade_subject=offering, name="A", code="SEC3-PHY-A", monthly_fee=Decimal("500.00")
        )
        self.student = create_student(
            full_name="أحمد محمد", grade=grade, guardian_phone="01012345678"
        )
        self.card = StudentCard.objects.create(
            card_number="CARD-1", qr_token="CMS1:aaaaaaaaaaaaaaaaaaaaaa"
        )
        assign_card(self.card, self.student)
        assign_svc.assign_student(self.student, self.group)

        start = timezone.now()
        self.lesson = open_lesson(create_lesson(self.group, start, start + timedelta(hours=2)))

    def _scan(self, _index):
        try:
            return services.scan(lesson_id=self.lesson.pk, qr_token=self.card.qr_token)["code"]
        except Exception as exc:  # noqa: BLE001 - the loser's code is the assertion
            return getattr(exc, "code", "ERROR")
        finally:
            connections.close_all()

    def test_two_scanners_produce_exactly_one_attendance_row(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(self._scan, range(2)))

        assert Attendance.objects.filter(lesson=self.lesson, student=self.student).count() == 1
        assert "OK_CHECK_IN" in codes
        assert any(code in {"WARN_DUPLICATE", "OK_CHECK_IN"} for code in codes)

    def test_eight_simultaneous_scans_still_produce_one_row(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(self._scan, range(8)))
        assert Attendance.objects.filter(lesson=self.lesson).count() == 1
