import json
from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.academics.models import EducationalStage, Grade, GradeSubject, Group, Subject
from apps.accounts.models import Role
from apps.core.http import DomainError
from apps.core.models import AuditAction, AuditLog
from apps.students import assignment_services as svc
from apps.students.models import AssignmentStatus, StudentGroupAssignment
from apps.students.services import create_student

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def world(db):
    stage = EducationalStage.objects.create(name="Secondary", code="SEC")
    grade = Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3")
    other_grade = Grade.objects.create(stage=stage, name="Grade 2 Secondary", code="SEC2")
    physics = Subject.objects.create(name="Physics", code="PHY")
    maths = Subject.objects.create(name="Mathematics", code="MATH")

    physics_sec3 = GradeSubject.objects.create(
        grade=grade, subject=physics, default_monthly_fee=Decimal("500.00")
    )
    maths_sec3 = GradeSubject.objects.create(
        grade=grade, subject=maths, default_monthly_fee=Decimal("400.00")
    )
    physics_sec2 = GradeSubject.objects.create(grade=other_grade, subject=physics)

    return {
        "grade": grade,
        "other_grade": other_grade,
        "physics_sec3": physics_sec3,
        "maths_sec3": maths_sec3,
        "physics_a": Group.objects.create(
            grade_subject=physics_sec3,
            name="Group A",
            code="SEC3-PHY-A",
            monthly_fee=Decimal("500.00"),
            capacity=2,
        ),
        "physics_b": Group.objects.create(
            grade_subject=physics_sec3,
            name="Group B",
            code="SEC3-PHY-B",
            monthly_fee=Decimal("500.00"),
        ),
        "maths_c": Group.objects.create(
            grade_subject=maths_sec3,
            name="Group C",
            code="SEC3-MATH-C",
            monthly_fee=Decimal("400.00"),
        ),
        "physics_sec2_a": Group.objects.create(
            grade_subject=physics_sec2, name="Group A", code="SEC2-PHY-A"
        ),
    }


@pytest.fixture
def student(world):
    return create_student(full_name="أحمد محمد", grade=world["grade"], guardian_phone="01012345678")


@pytest.fixture
def admin_client_(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _delete(client, url, payload):
    return client.delete(url, data=json.dumps(payload), content_type="application/json")


# --------------------------------------------------------------------------- #
# Constraints
# --------------------------------------------------------------------------- #


def test_a_student_can_study_several_subjects_at_once(world, student):
    svc.assign_student(student, world["physics_a"])
    svc.assign_student(student, world["maths_c"])
    active = student.assignments.filter(status=AssignmentStatus.ACTIVE)
    assert active.count() == 2
    assert {a.grade_subject.subject.code for a in active} == {"PHY", "MATH"}


def test_second_active_assignment_for_one_offering_is_rejected_by_the_database(world, student):
    svc.assign_student(student, world["physics_a"])
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            StudentGroupAssignment.objects.create(
                student=student,
                group=world["physics_b"],
                grade_subject=world["physics_sec3"],
            )


def test_service_turns_that_into_a_clean_error(world, student):
    svc.assign_student(student, world["physics_a"])
    with pytest.raises(DomainError) as exc:
        svc.assign_student(student, world["physics_b"])
    assert exc.value.code == "ERR_ALREADY_ASSIGNED"


def test_grade_subject_is_always_derived_from_the_group(world, student):
    assignment, _ = svc.assign_student(student, world["physics_a"])
    assignment.grade_subject = world["maths_sec3"]  # try to desynchronise
    assignment.save()
    assignment.refresh_from_db()
    assert assignment.grade_subject_id == world["physics_a"].grade_subject_id


def test_assigning_to_another_grades_group_is_blocked(world, student):
    with pytest.raises(DomainError) as exc:
        svc.assign_student(student, world["physics_sec2_a"])
    assert exc.value.code == "ERR_GRADE_MISMATCH"

    assignment, _ = svc.assign_student(student, world["physics_sec2_a"], allow_grade_mismatch=True)
    assert assignment.pk is not None


def test_capacity_warns_but_never_blocks(world, grade=None):
    group = world["physics_a"]  # capacity = 2
    students = [
        create_student(full_name=f"طالب {i}", grade=world["grade"], guardian_phone="01012345678")
        for i in range(3)
    ]
    warnings = [svc.assign_student(s, group)[1] for s in students]
    assert warnings[0] == [] and warnings[1] == []
    assert warnings[2] == ["WARN_CAPACITY"]
    assert group.assignments.filter(status=AssignmentStatus.ACTIVE).count() == 3


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def test_ending_an_assignment_keeps_the_row(world, student):
    assignment, _ = svc.assign_student(student, world["physics_a"])
    svc.end_assignment(assignment, reason="LEFT_CENTER")

    assignment.refresh_from_db()
    assert assignment.status == AssignmentStatus.ENDED
    assert assignment.end_date is not None
    assert assignment.group_id == world["physics_a"].pk
    assert StudentGroupAssignment.objects.count() == 1


def test_after_ending_the_student_can_join_another_group(world, student):
    assignment, _ = svc.assign_student(student, world["physics_a"])
    svc.end_assignment(assignment, reason="SCHEDULE")
    svc.assign_student(student, world["physics_b"])
    assert student.assignments.filter(status=AssignmentStatus.ACTIVE).count() == 1


def test_transfer_produces_two_rows_and_preserves_the_old_group(world, student, user_factory):
    actor = user_factory(username="admin4")
    assignment, _ = svc.assign_student(student, world["physics_a"], actor=actor)

    new_assignment, _ = svc.transfer_student(
        assignment, world["physics_b"], actor=actor, reason="تعارض مواعيد"
    )

    assignment.refresh_from_db()
    assert assignment.status == AssignmentStatus.TRANSFERRED
    assert assignment.group_id == world["physics_a"].pk  # history intact
    assert new_assignment.group_id == world["physics_b"].pk
    assert new_assignment.status == AssignmentStatus.ACTIVE
    assert student.assignments.count() == 2
    assert AuditLog.objects.filter(action=AuditAction.STUDENT_TRANSFERRED).exists()


def test_transfer_requires_a_reason(world, student):
    assignment, _ = svc.assign_student(student, world["physics_a"])
    with pytest.raises(DomainError) as exc:
        svc.transfer_student(assignment, world["physics_b"], reason="")
    assert exc.value.code == "ERR_REASON_REQUIRED"


def test_transfer_across_offerings_is_rejected(world, student):
    assignment, _ = svc.assign_student(student, world["physics_a"])
    with pytest.raises(DomainError) as exc:
        svc.transfer_student(assignment, world["maths_c"], reason="خطأ")
    assert exc.value.code == "ERR_DIFFERENT_OFFERING"


def test_transfer_to_the_same_group_is_rejected(world, student):
    assignment, _ = svc.assign_student(student, world["physics_a"])
    with pytest.raises(DomainError) as exc:
        svc.transfer_student(assignment, world["physics_a"], reason="بدون سبب")
    assert exc.value.code == "ERR_SAME_GROUP"


def test_active_assignment_lookup_is_a_single_row(world, student, django_assert_num_queries):
    svc.assign_student(student, world["physics_a"])
    with django_assert_num_queries(1):
        found = svc.active_assignment(student, world["physics_sec3"].pk)
    assert found.group_id == world["physics_a"].pk

    assert svc.active_assignment(student, world["maths_sec3"].pk) is None


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_group_roster_endpoint(admin_client_, world, student):
    svc.assign_student(student, world["physics_a"])
    response = admin_client_.get(
        reverse("students_api:group_students", args=[world["physics_a"].pk])
    )
    data = response.json()["data"]
    assert data["count"] == 1
    assert data["capacity"] == 2
    assert data["results"][0]["student_code"] == student.student_code


def test_assign_endpoint_and_monthly_total(admin_client_, world, student):
    for group in (world["physics_a"], world["maths_c"]):
        response = _post(
            admin_client_,
            reverse("students_api:group_students", args=[group.pk]),
            {"student_id": student.pk},
        )
        assert response.status_code == 200

    summary = admin_client_.get(
        reverse("students_api:student_assignments", args=[student.pk])
    ).json()["data"]
    assert len(summary["active"]) == 2
    assert summary["monthly_total"] == "900.00"


def test_assign_endpoint_accepts_a_date_string(admin_client_, world, student):
    """A JSON date must land on the instance as a real date, not a string."""
    response = _post(
        admin_client_,
        reverse("students_api:group_students", args=[world["physics_a"].pk]),
        {"student_id": student.pk, "start_date": "2026-06-01"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["assignment"]["start_date"] == "2026-06-01"
    assert StudentGroupAssignment.objects.get().start_date == date(2026, 6, 1)


def test_assign_endpoint_rejects_a_bad_date(admin_client_, world, student):
    response = _post(
        admin_client_,
        reverse("students_api:group_students", args=[world["physics_a"].pk]),
        {"student_id": student.pk, "start_date": "01/06/2026"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "ERR_VALIDATION"


def test_delete_endpoint_is_soft(admin_client_, world, student):
    svc.assign_student(student, world["physics_a"])
    response = _delete(
        admin_client_,
        reverse("students_api:group_student_detail", args=[world["physics_a"].pk, student.pk]),
        {"reason": "LEFT_CENTER"},
    )
    assert response.status_code == 200
    assert StudentGroupAssignment.objects.count() == 1
    assert StudentGroupAssignment.objects.get().status == AssignmentStatus.ENDED


def test_transfer_endpoint(admin_client_, world, student):
    assignment, _ = svc.assign_student(student, world["physics_a"])
    response = _post(
        admin_client_,
        reverse("students_api:transfer", args=[assignment.pk]),
        {"new_group_id": world["physics_b"].pk, "reason": "طلب الطالب"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["assignment"]["group_code"] == "SEC3-PHY-B"


def test_cashier_cannot_assign(client, user_factory, world, student):
    call_command("seed_roles", verbosity=0)
    user_factory(username="cash", role=Role.CASHIER, password=PASSWORD)
    client.login(username="cash", password=PASSWORD)
    response = _post(
        client,
        reverse("students_api:group_students", args=[world["physics_a"].pk]),
        {"student_id": student.pk},
    )
    assert response.status_code == 403
