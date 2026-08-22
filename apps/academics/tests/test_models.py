from datetime import time
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from apps.academics.models import (
    EducationalStage,
    Grade,
    GradeSubject,
    Group,
    GroupSchedule,
    Subject,
    Weekday,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def tree():
    stage = EducationalStage.objects.create(
        name="Secondary", name_ar="الثانوية", code="SEC", order=3
    )
    grade = Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3", order=3)
    subject = Subject.objects.create(name="Physics", name_ar="الفيزياء", code="PHY")
    offering = GradeSubject.objects.create(
        grade=grade, subject=subject, default_monthly_fee=Decimal("500.00")
    )
    group = Group.objects.create(
        grade_subject=offering, name="Group A", code="SEC3-PHY-A", monthly_fee=Decimal("500.00")
    )
    return {
        "stage": stage,
        "grade": grade,
        "subject": subject,
        "offering": offering,
        "group": group,
    }


def test_grade_name_is_unique_within_a_stage(tree):
    with pytest.raises(IntegrityError):
        Grade.objects.create(stage=tree["stage"], name="Grade 3 Secondary", code="OTHER")


def test_same_grade_name_allowed_under_a_different_stage(tree):
    other_stage = EducationalStage.objects.create(name="Preparatory", code="PREP")
    Grade.objects.create(stage=other_stage, name="Grade 3 Secondary", code="PREP3")
    assert Grade.objects.filter(name="Grade 3 Secondary").count() == 2


def test_offering_pair_is_unique(tree):
    with pytest.raises(IntegrityError):
        GradeSubject.objects.create(grade=tree["grade"], subject=tree["subject"])


def test_subject_can_be_offered_to_several_grades_at_different_fees(tree):
    grade2 = Grade.objects.create(stage=tree["stage"], name="Grade 2 Secondary", code="SEC2")
    second = GradeSubject.objects.create(
        grade=grade2, subject=tree["subject"], default_monthly_fee=Decimal("450.00")
    )
    assert second.default_monthly_fee != tree["offering"].default_monthly_fee
    assert tree["subject"].offerings.count() == 2


def test_negative_offering_fee_rejected_by_the_database(tree):
    grade2 = Grade.objects.create(stage=tree["stage"], name="Grade 1 Secondary", code="SEC1")
    with pytest.raises(IntegrityError):
        GradeSubject.objects.create(
            grade=grade2, subject=tree["subject"], default_monthly_fee=Decimal("-1.00")
        )


def test_group_name_is_unique_within_an_offering(tree):
    with pytest.raises(IntegrityError):
        Group.objects.create(grade_subject=tree["offering"], name="Group A", code="SEC3-PHY-A-DUP")


def test_group_derives_grade_subject_and_stage_without_extra_columns(tree):
    group = tree["group"]
    assert group.subject == tree["subject"]
    assert group.grade == tree["grade"]
    assert group.stage == tree["stage"]
    field_names = {f.name for f in Group._meta.get_fields()}
    assert "grade" not in field_names and "subject" not in field_names


def test_stage_with_grades_cannot_be_deleted(tree):
    with pytest.raises(ProtectedError):
        tree["stage"].delete()


def test_schedule_requires_end_after_start(tree):
    with pytest.raises(IntegrityError):
        GroupSchedule.objects.create(
            group=tree["group"],
            weekday=Weekday.SATURDAY,
            start_time=time(18, 0),
            end_time=time(16, 0),
        )


def test_schedule_slot_is_unique(tree):
    GroupSchedule.objects.create(
        group=tree["group"], weekday=Weekday.SATURDAY, start_time=time(16, 0), end_time=time(18, 0)
    )
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            GroupSchedule.objects.create(
                group=tree["group"],
                weekday=Weekday.SATURDAY,
                start_time=time(16, 0),
                end_time=time(19, 0),
            )


def test_weekday_mapping_matches_the_egyptian_week():
    import datetime

    # 22 Aug 2026 is a Saturday.
    assert Weekday.from_python_weekday(datetime.date(2026, 8, 22).weekday()) == Weekday.SATURDAY
    assert Weekday.from_python_weekday(datetime.date(2026, 8, 23).weekday()) == Weekday.SUNDAY
    assert Weekday.from_python_weekday(datetime.date(2026, 8, 28).weekday()) == Weekday.FRIDAY


def test_seed_academics_is_idempotent():
    from django.core.management import call_command

    call_command("seed_academics", "--demo", "--force", verbosity=0)
    counts = (
        EducationalStage.objects.count(),
        Grade.objects.count(),
        Subject.objects.count(),
        GradeSubject.objects.count(),
        Group.objects.count(),
        GroupSchedule.objects.count(),
    )
    call_command("seed_academics", "--demo", "--force", verbosity=0)
    assert counts == (
        EducationalStage.objects.count(),
        Grade.objects.count(),
        Subject.objects.count(),
        GradeSubject.objects.count(),
        Group.objects.count(),
        GroupSchedule.objects.count(),
    )
    # The brief's example: 3rd secondary Physics with groups A, B and C.
    physics_sec3 = GradeSubject.objects.get(grade__code="SEC3", subject__code="PHY")
    assert physics_sec3.groups.count() == 3
