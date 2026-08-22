import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.core.management import call_command
from django.db import connection
from django.urls import reverse

from apps.academics.models import EducationalStage, Grade
from apps.accounts.models import Role
from apps.core.models import AuditAction, AuditLog
from apps.core.text import normalize_arabic
from apps.students import services
from apps.students.models import Student, StudentStatus

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def grade(db):
    stage = EducationalStage.objects.create(name="Secondary", code="SEC", order=3)
    return Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3")


@pytest.fixture
def admin_client_(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _patch(client, url, payload):
    return client.patch(url, data=json.dumps(payload), content_type="application/json")


def make_student(grade, **extra):
    fields = {
        "full_name": "أحمد محمد علي",
        "grade": grade,
        "guardian_phone": "01012345678",
        **extra,
    }
    return services.create_student(**fields)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


def test_student_table_has_no_group_card_or_paid_columns():
    """Principles 1–4 expressed as a schema guard."""
    field_names = {f.name for f in Student._meta.get_fields()}
    assert "group" not in field_names
    assert "card" not in field_names
    assert not any("paid" in name for name in field_names)


def test_stage_is_derived_from_grade(grade):
    student = make_student(grade)
    assert student.stage == grade.stage
    assert "stage" not in {f.name for f in Student._meta.get_fields()}


def test_photo_path_is_not_sequential(grade):
    from apps.students.models import student_photo_path

    student = make_student(grade)
    path_one = student_photo_path(student, "photo.jpg")
    path_two = student_photo_path(student, "photo.jpg")
    assert path_one != path_two

    stem = path_one.rsplit("/", 1)[-1].split(".")[0]
    assert len(stem) == 32 and all(c in "0123456789abcdef" for c in stem)
    assert stem != str(student.pk)


# --------------------------------------------------------------------------- #
# Codes
# --------------------------------------------------------------------------- #


def test_student_code_format_and_increment(grade):
    first = make_student(grade)
    second = make_student(grade, full_name="سارة علي حسن")
    assert first.student_code == "STD-000001"
    assert second.student_code == "STD-000002"


@pytest.mark.skipif(connection.vendor == "sqlite", reason="threaded writes need PostgreSQL")
@pytest.mark.postgres
def test_concurrent_creation_produces_unique_codes(grade):
    def create(index):
        return make_student(grade, full_name=f"طالب رقم {index}").student_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(create, range(20)))
    assert len(set(codes)) == 20


# --------------------------------------------------------------------------- #
# Services
# --------------------------------------------------------------------------- #


def test_create_is_audited(grade, user_factory):
    actor = user_factory(username="reception")
    student = services.create_student(
        full_name="أحمد محمد", grade=grade, guardian_phone="01012345678", actor=actor
    )
    entry = AuditLog.objects.get(action=AuditAction.STUDENT_CREATED, object_id=student.pk)
    assert entry.actor == actor
    assert entry.changes["full_name"] == "أحمد محمد"


def test_update_records_only_the_changed_fields(grade, user_factory):
    student = make_student(grade)
    actor = user_factory(username="reception2")
    services.update_student(student, actor=actor, school="مدرسة النيل", reason="تحديث بيانات")

    entry = AuditLog.objects.get(action=AuditAction.STUDENT_UPDATED, object_id=student.pk)
    assert set(entry.changes) == {"school"}
    assert entry.changes["school"]["new"] == "مدرسة النيل"


def test_status_change_requires_a_reason(grade):
    from apps.core.http import DomainError

    student = make_student(grade)
    with pytest.raises(DomainError) as exc:
        services.change_status(student, StudentStatus.SUSPENDED, reason="")
    assert exc.value.code == "ERR_REASON_REQUIRED"


def test_status_change_is_audited(grade, user_factory):
    student = make_student(grade)
    actor = user_factory(username="admin2")
    services.change_status(student, StudentStatus.SUSPENDED, actor=actor, reason="عدم الالتزام")
    entry = AuditLog.objects.get(action=AuditAction.STUDENT_STATUS_CHANGED, object_id=student.pk)
    assert entry.changes["status"] == {"old": "ACTIVE", "new": "SUSPENDED"}
    assert entry.reason == "عدم الالتزام"


def test_graduated_students_remain_readable(grade):
    student = make_student(grade)
    services.change_status(student, StudentStatus.GRADUATED, reason="أنهى الثانوية")
    student.refresh_from_db()
    assert student.status == StudentStatus.GRADUATED
    assert Student.objects.filter(pk=student.pk).exists()
    assert student.can_attend is False


# --------------------------------------------------------------------------- #
# Arabic search
# --------------------------------------------------------------------------- #


def test_normalisation_folds_alef_yaa_and_diacritics():
    assert normalize_arabic("أَحْمَد") == "احمد"
    assert normalize_arabic("إسلام") == "اسلام"
    assert normalize_arabic("مصطفى") == "مصطفي"
    assert normalize_arabic("فاطمة") == "فاطمه"


def test_search_matches_regardless_of_alef_form(grade):
    make_student(grade, full_name="أحمد محمد")
    results = services.search_students(Student.objects.all(), "احمد")
    assert results.count() == 1

    results = services.search_students(Student.objects.all(), "أحمد")
    assert results.count() == 1


def test_search_matches_code_and_guardian_phone(grade):
    student = make_student(grade)
    assert services.search_students(Student.objects.all(), student.student_code).count() == 1
    assert services.search_students(Student.objects.all(), "0101234").count() == 1


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_create_student_endpoint(admin_client_, grade):
    response = _post(
        admin_client_,
        reverse("students_api:students"),
        {"full_name": "أحمد محمد علي", "grade": grade.pk, "guardian_phone": "01012345678"},
    )
    assert response.status_code == 200
    body = response.json()["data"]["student"]
    assert body["student_code"].startswith("STD-")
    assert body["status"] == "ACTIVE"


def test_invalid_phone_is_a_field_error(admin_client_, grade):
    response = _post(
        admin_client_,
        reverse("students_api:students"),
        {"full_name": "أحمد محمد", "grade": grade.pk, "guardian_phone": "12345"},
    )
    assert response.status_code == 400
    assert "guardian_phone" in response.json()["field_errors"]


def test_guardian_phone_is_required_by_the_form(admin_client_, grade):
    response = _post(
        admin_client_,
        reverse("students_api:students"),
        {"full_name": "أحمد محمد", "grade": grade.pk},
    )
    assert response.status_code == 400
    assert "guardian_phone" in response.json()["field_errors"]


def test_patch_changes_only_supplied_fields(admin_client_, grade):
    student = make_student(grade)
    response = _patch(
        admin_client_,
        reverse("students_api:student_detail", args=[student.pk]),
        {"school": "مدرسة النيل", "reason": "تحديث"},
    )
    assert response.status_code == 200
    student.refresh_from_db()
    assert student.school == "مدرسة النيل"
    assert student.full_name == "أحمد محمد علي"


def test_list_is_paginated_and_filterable(admin_client_, grade):
    for index in range(30):
        make_student(grade, full_name=f"طالب {index}")
    response = admin_client_.get(reverse("students_api:students"))
    data = response.json()["data"]
    assert data["count"] == 30
    assert len(data["results"]) == 25
    assert data["has_next"] is True

    filtered = admin_client_.get(reverse("students_api:students") + "?status=SUSPENDED")
    assert filtered.json()["data"]["count"] == 0


def test_list_query_count_is_bounded(admin_client_, grade, django_assert_num_queries):
    for index in range(30):
        make_student(grade, full_name=f"طالب {index}")
    # session + user + permissions + count + page  — no N+1 over grades/stages
    with django_assert_num_queries(6):
        admin_client_.get(reverse("students_api:students"))


def test_typeahead_limits_and_needs_two_characters(admin_client_, grade):
    for index in range(15):
        make_student(grade, full_name=f"أحمد {index}")
    assert (
        admin_client_.get(reverse("students_api:student_search") + "?q=a").json()["data"]["results"]
        == []
    )
    results = admin_client_.get(reverse("students_api:student_search") + "?q=أحمد").json()["data"][
        "results"
    ]
    assert len(results) == 10


def test_status_endpoint_requires_reason(admin_client_, grade):
    student = make_student(grade)
    response = _post(
        admin_client_,
        reverse("students_api:student_status", args=[student.pk]),
        {"status": "SUSPENDED"},
    )
    assert response.status_code == 400
    assert response.json()["code"] == "ERR_REASON_REQUIRED"


def test_cashier_can_view_but_not_create(client, user_factory, grade):
    call_command("seed_roles", verbosity=0)
    user_factory(username="cash", role=Role.CASHIER, password=PASSWORD)
    client.login(username="cash", password=PASSWORD)

    assert client.get(reverse("students_api:students")).status_code == 200
    response = _post(
        client,
        reverse("students_api:students"),
        {"full_name": "أحمد محمد", "grade": grade.pk, "guardian_phone": "01012345678"},
    )
    assert response.status_code == 403


def test_pages_render(admin_client_, grade):
    student = make_student(grade)
    assert admin_client_.get(reverse("students:list")).status_code == 200
    assert admin_client_.get(reverse("students:create")).status_code == 200
    assert admin_client_.get(reverse("students:detail", args=[student.pk])).status_code == 200
