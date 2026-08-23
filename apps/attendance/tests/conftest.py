"""Shared fixtures for the attendance suite."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.academics.models import EducationalStage, Grade, GradeSubject, Group, Subject
from apps.accounts.models import Role
from apps.cards.models import StudentCard
from apps.cards.services import assign_card
from apps.lessons.services import create_lesson, open_lesson
from apps.students import assignment_services as assign_svc
from apps.students.services import create_student

PASSWORD = "TestPass!2026"


@pytest.fixture
def world(db):
    """One offering, three groups, plus a second grade for scenario D."""
    stage = EducationalStage.objects.create(name="Secondary", code="SEC")
    grade = Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3")
    other_grade = Grade.objects.create(stage=stage, name="Grade 2 Secondary", code="SEC2")
    physics = Subject.objects.create(name="Physics", code="PHY")
    chemistry = Subject.objects.create(name="Chemistry", code="CHEM")

    physics_sec3 = GradeSubject.objects.create(
        grade=grade, subject=physics, default_monthly_fee=Decimal("500.00")
    )
    chemistry_sec3 = GradeSubject.objects.create(
        grade=grade, subject=chemistry, default_monthly_fee=Decimal("450.00")
    )
    physics_sec2 = GradeSubject.objects.create(grade=other_grade, subject=physics)

    return {
        "grade": grade,
        "other_grade": other_grade,
        "physics_sec3": physics_sec3,
        "chemistry_sec3": chemistry_sec3,
        "group_a": Group.objects.create(
            grade_subject=physics_sec3,
            name="Group A",
            code="SEC3-PHY-A",
            monthly_fee=Decimal("500.00"),
        ),
        "group_b": Group.objects.create(
            grade_subject=physics_sec3,
            name="Group B",
            code="SEC3-PHY-B",
            monthly_fee=Decimal("500.00"),
        ),
        "chem_a": Group.objects.create(
            grade_subject=chemistry_sec3,
            name="Group A",
            code="SEC3-CHEM-A",
            monthly_fee=Decimal("450.00"),
        ),
        "sec2_group": Group.objects.create(
            grade_subject=physics_sec2, name="Group A", code="SEC2-PHY-A"
        ),
    }


@pytest.fixture
def student(world):
    return create_student(full_name="أحمد محمد", grade=world["grade"], guardian_phone="01012345678")


@pytest.fixture
def carded_student(world, student):
    card = StudentCard.objects.create(
        card_number="CARD-000001", qr_token="CMS1:aaaaaaaaaaaaaaaaaaaaaa"
    )
    assign_card(card, student)
    return student, card


@pytest.fixture
def assigned_student(world, carded_student):
    """Student whose usual Physics group is Group A."""
    student, card = carded_student
    assign_svc.assign_student(student, world["group_a"])
    return student, card


def make_lesson(group, *, start=None, open_it=True, minutes=120):
    start = start or timezone.now()
    lesson = create_lesson(group, start, start + timedelta(minutes=minutes))
    if open_it:
        open_lesson(lesson)
        lesson.refresh_from_db()
    return lesson


@pytest.fixture
def lesson_a(world):
    """An open lesson of Group A, starting now."""
    return make_lesson(world["group_a"])


@pytest.fixture
def lesson_b(world):
    return make_lesson(world["group_b"])


@pytest.fixture
def operator(user_factory, db):
    call_command("seed_roles", verbosity=0)
    return user_factory(username="op", role=Role.SCAN_OPERATOR, password=PASSWORD)


@pytest.fixture
def admin_client_(client, user_factory, db):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client
