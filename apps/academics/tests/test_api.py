import json
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.academics.models import EducationalStage, Grade, GradeSubject, Group, Subject
from apps.accounts.models import Role

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def structure(db):
    stage = EducationalStage.objects.create(name="Secondary", code="SEC", order=3)
    grade = Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3")
    other_grade = Grade.objects.create(stage=stage, name="Grade 2 Secondary", code="SEC2")
    subject = Subject.objects.create(name="Physics", code="PHY")
    offering = GradeSubject.objects.create(
        grade=grade, subject=subject, default_monthly_fee=Decimal("500.00")
    )
    return {
        "stage": stage,
        "grade": grade,
        "other_grade": other_grade,
        "subject": subject,
        "offering": offering,
    }


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


def test_create_stage_and_reject_duplicate(admin_client_):
    url = reverse("academics_api:stages")
    assert (
        _post(admin_client_, url, {"name": "Primary", "code": "PRI", "order": 1}).status_code == 200
    )

    duplicate = _post(admin_client_, url, {"name": "Primary", "code": "PRI2", "order": 2})
    assert duplicate.status_code == 400
    assert "name" in duplicate.json()["field_errors"]


def test_grades_feeder_filters_by_stage(admin_client_, structure):
    other_stage = EducationalStage.objects.create(name="Prep", code="PREP")
    Grade.objects.create(stage=other_stage, name="Grade 1 Prep", code="PREP1")

    response = admin_client_.get(
        reverse("academics_api:grades") + f"?stage={structure['stage'].pk}"
    )
    codes = {row["code"] for row in response.json()["data"]["results"]}
    assert codes == {"SEC3", "SEC2"}


def test_offerings_feeder_filters_by_grade(admin_client_, structure):
    response = admin_client_.get(
        reverse("academics_api:offerings") + f"?grade={structure['grade'].pk}"
    )
    results = response.json()["data"]["results"]
    assert len(results) == 1
    assert results[0]["subject"] == "Physics"


def test_group_requires_an_existing_offering(admin_client_, structure):
    """A group cannot be created for a (grade, subject) pair nobody offers."""
    response = _post(
        admin_client_,
        reverse("academics_api:groups"),
        {"grade_subject": 9999, "name": "Group A", "code": "X-1"},
    )
    assert response.status_code == 400
    assert "grade_subject" in response.json()["field_errors"]


def test_group_inherits_the_offering_fee(admin_client_, structure):
    response = _post(
        admin_client_,
        reverse("academics_api:groups"),
        {"grade_subject": structure["offering"].pk, "name": "Group A", "code": "SEC3-PHY-A"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["group"]["monthly_fee"] == "500.00"


def test_duplicate_group_name_in_one_offering_is_rejected(admin_client_, structure):
    url = reverse("academics_api:groups")
    payload = {"grade_subject": structure["offering"].pk, "name": "Group A", "code": "SEC3-PHY-A"}
    assert _post(admin_client_, url, payload).status_code == 200

    payload["code"] = "SEC3-PHY-A2"
    duplicate = _post(admin_client_, url, payload)
    assert duplicate.status_code == 400
    assert duplicate.json()["field_errors"]


def test_patch_only_changes_supplied_fields(admin_client_, structure):
    group = Group.objects.create(
        grade_subject=structure["offering"],
        name="Group B",
        code="SEC3-PHY-B",
        capacity=30,
        monthly_fee=Decimal("500.00"),
    )
    response = _patch(
        admin_client_, reverse("academics_api:group_detail", args=[group.pk]), {"capacity": 40}
    )
    assert response.status_code == 200

    group.refresh_from_db()
    assert group.capacity == 40
    assert group.name == "Group B"
    assert group.monthly_fee == Decimal("500.00")


def test_raising_the_offering_fee_does_not_touch_existing_groups(admin_client_, structure):
    group = Group.objects.create(
        grade_subject=structure["offering"],
        name="Group C",
        code="SEC3-PHY-C",
        monthly_fee=Decimal("500.00"),
    )
    _patch(
        admin_client_,
        reverse("academics_api:offering_detail", args=[structure["offering"].pk]),
        {"default_monthly_fee": "600.00"},
    )
    group.refresh_from_db()
    assert group.monthly_fee == Decimal("500.00")


def test_cashier_can_read_but_not_write(client, user_factory, structure):
    call_command("seed_roles", verbosity=0)
    user_factory(username="cash", role=Role.CASHIER, password=PASSWORD)
    client.login(username="cash", password=PASSWORD)

    assert client.get(reverse("academics_api:groups")).status_code == 200
    response = _post(
        client,
        reverse("academics_api:groups"),
        {"grade_subject": structure["offering"].pk, "name": "X", "code": "X"},
    )
    assert response.status_code == 403


def test_instructor_only_sees_their_own_groups(client, user_factory, structure):
    from apps.academics.models import Instructor

    call_command("seed_roles", verbosity=0)
    teacher = user_factory(username="teach", role=Role.INSTRUCTOR, password=PASSWORD)
    mine = Instructor.objects.create(full_name="أ. محمد", user=teacher)
    theirs = Instructor.objects.create(full_name="أ. هدى")

    Group.objects.create(
        grade_subject=structure["offering"], name="Mine", code="G-MINE", instructor=mine
    )
    Group.objects.create(
        grade_subject=structure["offering"], name="Theirs", code="G-THEIRS", instructor=theirs
    )

    client.login(username="teach", password=PASSWORD)
    results = client.get(reverse("academics_api:groups")).json()["data"]["results"]
    assert [row["code"] for row in results] == ["G-MINE"]


def test_instructor_gets_404_not_403_for_another_groups_page(client, user_factory, structure):
    from apps.academics.models import Instructor

    call_command("seed_roles", verbosity=0)
    teacher = user_factory(username="teach2", role=Role.INSTRUCTOR, password=PASSWORD)
    Instructor.objects.create(full_name="أ. محمد", user=teacher)
    theirs = Instructor.objects.create(full_name="أ. هدى")
    foreign = Group.objects.create(
        grade_subject=structure["offering"], name="Theirs", code="G-THEIRS", instructor=theirs
    )

    client.login(username="teach2", password=PASSWORD)
    assert client.get(reverse("academics:group_detail", args=[foreign.pk])).status_code == 404


def test_structure_and_group_pages_render(admin_client_, structure):
    assert admin_client_.get(reverse("academics:structure")).status_code == 200
    assert admin_client_.get(reverse("academics:groups")).status_code == 200
    group = Group.objects.create(
        grade_subject=structure["offering"], name="Group A", code="SEC3-PHY-A"
    )
    assert admin_client_.get(reverse("academics:group_detail", args=[group.pk])).status_code == 200
