import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.academics.models import EducationalStage, Grade
from apps.accounts.models import Role
from apps.cards import services
from apps.cards.models import CardAssignment, CardStatus, StudentCard
from apps.cards.tokens import generate_token, mask_token, normalize_token
from apps.core.http import DomainError
from apps.core.models import AuditAction, AuditLog
from apps.students.services import create_student

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def grade(db):
    stage = EducationalStage.objects.create(name="Secondary", code="SEC")
    return Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3")


@pytest.fixture
def student(grade):
    return create_student(full_name="أحمد محمد", grade=grade, guardian_phone="01012345678")


@pytest.fixture
def other_student(grade):
    return create_student(full_name="سارة علي", grade=grade, guardian_phone="01112345678")


@pytest.fixture
def card(db):
    return StudentCard.objects.create(
        card_number="CARD-000001", qr_token="CMS1:aaaaaaaaaaaaaaaaaaaaaa"
    )


@pytest.fixture
def spare_card(db):
    return StudentCard.objects.create(
        card_number="CARD-000002", qr_token="CMS1:bbbbbbbbbbbbbbbbbbbbbb"
    )


@pytest.fixture
def admin_client_(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #


def test_generated_tokens_are_prefixed_unique_and_long_enough():
    tokens = {generate_token() for _ in range(2000)}
    assert len(tokens) == 2000
    sample = tokens.pop()
    assert sample.startswith("CMS1:")
    assert len(sample.split(":")[1]) == 22


def test_normalisation_strips_hid_scanner_noise():
    assert normalize_token("  CMS1:abc123\r\n") == "CMS1:abc123"


def test_oversized_or_malformed_tokens_are_rejected_before_the_database():
    with pytest.raises(DomainError) as exc:
        normalize_token("x" * 200)
    assert exc.value.code == "ERR_CARD_NOT_FOUND"

    with pytest.raises(DomainError):
        normalize_token("CMS1:abc'; DROP TABLE cards_studentcard;--")

    with pytest.raises(DomainError):
        normalize_token("")


def test_masking_never_reveals_the_whole_token():
    token = "CMS1:7f92c8a14e314d919a82"
    masked = mask_token(token)
    assert token not in masked
    assert masked.startswith("CMS1:7f")


# --------------------------------------------------------------------------- #
# Constraints
# --------------------------------------------------------------------------- #


def test_assigned_card_must_have_a_holder(card):
    card.status = CardStatus.ASSIGNED
    with pytest.raises(IntegrityError):
        card.save()


def test_unassigned_card_must_not_have_a_holder(card, student):
    card.current_student = student
    with pytest.raises(IntegrityError):
        card.save()


def test_token_is_unique(card):
    with pytest.raises(IntegrityError):
        StudentCard.objects.create(card_number="CARD-999", qr_token=card.qr_token)


def test_one_open_history_row_per_student(card, spare_card, student):
    from django.utils import timezone

    CardAssignment.objects.create(card=card, student=student, assigned_at=timezone.now())
    with transaction.atomic():
        with pytest.raises(IntegrityError):
            CardAssignment.objects.create(
                card=spare_card, student=student, assigned_at=timezone.now()
            )


# --------------------------------------------------------------------------- #
# Assignment
# --------------------------------------------------------------------------- #


def test_assign_sets_status_holder_and_history(card, student, user_factory):
    actor = user_factory(username="reception")
    services.assign_card(card, student, actor=actor)

    card.refresh_from_db()
    assert card.status == CardStatus.ASSIGNED
    assert card.current_student == student
    assert card.issued_at is not None

    history = CardAssignment.objects.get(card=card, student=student)
    assert history.released_at is None
    assert AuditLog.objects.filter(action=AuditAction.CARD_ASSIGNED, object_id=card.pk).exists()


def test_cannot_assign_a_card_that_is_already_taken(card, student, other_student):
    services.assign_card(card, student)
    with pytest.raises(DomainError) as exc:
        services.assign_card(card, other_student)
    assert exc.value.code == "ERR_CARD_ALREADY_ASSIGNED"


def test_cannot_give_a_student_a_second_active_card(card, spare_card, student):
    services.assign_card(card, student)
    with pytest.raises(DomainError) as exc:
        services.assign_card(spare_card, student)
    assert exc.value.code == "ERR_STUDENT_HAS_CARD"


@pytest.mark.parametrize(
    "status,expected",
    [
        (CardStatus.LOST, "ERR_CARD_LOST"),
        (CardStatus.DISABLED, "ERR_CARD_DISABLED"),
        (CardStatus.REPLACED, "ERR_CARD_REPLACED"),
    ],
)
def test_unusable_cards_have_distinct_error_codes(card, student, status, expected):
    card.status = status
    card.save()
    with pytest.raises(DomainError) as exc:
        services.assign_card(card, student)
    assert exc.value.code == expected

    with pytest.raises(DomainError) as usable_exc:
        services.assert_usable(card)
    assert usable_exc.value.code == expected


def test_available_card_is_not_usable_for_attendance(card):
    with pytest.raises(DomainError) as exc:
        services.assert_usable(card)
    assert exc.value.code == "ERR_CARD_UNASSIGNED"


# --------------------------------------------------------------------------- #
# Loss and replacement
# --------------------------------------------------------------------------- #


def test_mark_lost_requires_reason_and_frees_the_student(card, student):
    services.assign_card(card, student)
    with pytest.raises(DomainError) as exc:
        services.mark_lost(card, reason="")
    assert exc.value.code == "ERR_REASON_REQUIRED"

    services.mark_lost(card, reason="فقدها الطالب")
    card.refresh_from_db()
    assert card.status == CardStatus.LOST
    assert card.current_student is None
    assert card.lost_at is not None
    assert CardAssignment.objects.get(card=card).released_at is not None


def test_replacement_moves_the_student_and_keeps_history(card, spare_card, student, user_factory):
    actor = user_factory(username="admin3")
    services.assign_card(card, student, actor=actor)

    old_card, new_card = services.replace_card(
        card, spare_card, actor=actor, reason="فقد البطاقة", old_status=CardStatus.LOST
    )

    assert old_card.status == CardStatus.LOST
    assert old_card.current_student is None
    assert old_card.replaced_by_id == new_card.pk
    assert new_card.status == CardStatus.ASSIGNED
    assert new_card.current_student_id == student.pk

    history = list(CardAssignment.objects.filter(student=student).order_by("assigned_at"))
    assert len(history) == 2
    assert history[0].released_at is not None
    assert history[1].released_at is None
    assert AuditLog.objects.filter(action=AuditAction.CARD_REPLACED).exists()


def test_old_token_stops_working_immediately_after_replacement(card, spare_card, student):
    services.assign_card(card, student)
    services.replace_card(card, spare_card, reason="فقد", old_status=CardStatus.LOST)

    old = services.find_card(card.qr_token)
    with pytest.raises(DomainError) as exc:
        services.assert_usable(old)
    assert exc.value.code == "ERR_CARD_LOST"

    new = services.find_card(spare_card.qr_token)
    assert services.assert_usable(new).current_student_id == student.pk


def test_replacement_requires_an_available_new_card(card, spare_card, student, other_student):
    services.assign_card(card, student)
    services.assign_card(spare_card, other_student)
    with pytest.raises(DomainError) as exc:
        services.replace_card(card, spare_card, reason="فقد")
    assert exc.value.code == "ERR_CARD_ALREADY_ASSIGNED"


# --------------------------------------------------------------------------- #
# Import / generate
# --------------------------------------------------------------------------- #


def test_import_is_all_or_nothing_on_a_duplicate(tmp_path):
    path = tmp_path / "batch.csv"
    path.write_text(
        "card_number,qr_token\nCARD-100,CMS1:tok100\nCARD-101,CMS1:tok101\nCARD-100,CMS1:tok102\n",
        encoding="utf-8",
    )
    with pytest.raises(CommandError) as exc:
        call_command("import_cards", str(path), verbosity=0)
    # Operator-facing message: the row number and the offending value.
    assert "CARD-100" in str(exc.value)
    assert "4" in str(exc.value)
    assert StudentCard.objects.count() == 0


def test_import_rejects_a_token_already_in_the_database(tmp_path, card):
    path = tmp_path / "batch.csv"
    path.write_text(f"card_number,qr_token\nCARD-200,{card.qr_token}\n", encoding="utf-8")
    with pytest.raises(CommandError):
        call_command("import_cards", str(path), verbosity=0)
    assert StudentCard.objects.count() == 1


def test_import_accepts_excel_bom_and_batch_label(tmp_path):
    path = tmp_path / "batch.csv"
    path.write_text(
        "﻿card_number,qr_token\nCARD-300,CMS1:tok300\nCARD-301,CMS1:tok301\n",
        encoding="utf-8",
    )
    call_command("import_cards", str(path), "--batch", "B7", verbosity=0)
    assert StudentCard.objects.filter(batch="B7").count() == 2
    assert StudentCard.objects.filter(status=CardStatus.AVAILABLE).count() == 2


def test_generate_cards_creates_available_stock():
    call_command("generate_cards", "--count", "25", "--batch", "B1", verbosity=0)
    cards = StudentCard.objects.filter(batch="B1")
    assert cards.count() == 25
    assert cards.filter(status=CardStatus.AVAILABLE).count() == 25
    assert len({c.qr_token for c in cards}) == 25
    assert cards.first().card_number.startswith("CARD-")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_lookup_reports_each_state_distinctly(admin_client_, card, student):
    url = reverse("cards_api:lookup")

    available = _post(admin_client_, url, {"qr_token": card.qr_token})
    assert available.status_code == 200
    assert available.json()["data"]["code"] == "OK_CARD_AVAILABLE"

    services.assign_card(card, student)
    taken = _post(admin_client_, url, {"qr_token": card.qr_token})
    assert taken.status_code == 409
    assert taken.json()["code"] == "ERR_CARD_ALREADY_ASSIGNED"

    unknown = _post(admin_client_, url, {"qr_token": "CMS1:nope"})
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "ERR_CARD_NOT_FOUND"


def test_lookup_never_returns_the_raw_token(admin_client_, card):
    response = _post(admin_client_, reverse("cards_api:lookup"), {"qr_token": card.qr_token})
    assert card.qr_token not in response.content.decode("utf-8")


def test_assign_endpoint_and_student_timeline(admin_client_, card, student):
    response = _post(
        admin_client_,
        reverse("cards_api:assign"),
        {"qr_token": card.qr_token, "student_id": student.pk},
    )
    assert response.status_code == 200

    timeline = admin_client_.get(reverse("cards_api:student_cards", args=[student.pk]))
    data = timeline.json()["data"]
    assert data["active_card"]["card_number"] == "CARD-000001"
    assert len(data["results"]) == 1


def test_replace_endpoint(admin_client_, card, spare_card, student):
    services.assign_card(card, student)
    response = _post(
        admin_client_,
        reverse("cards_api:replace", args=[card.pk]),
        {"new_qr_token": spare_card.qr_token, "reason": "فقد البطاقة", "old_status": "LOST"},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["old_card"]["status"] == "LOST"
    assert body["new_card"]["status"] == "ASSIGNED"


def test_mark_lost_endpoint_requires_reason(admin_client_, card, student):
    services.assign_card(card, student)
    response = _post(admin_client_, reverse("cards_api:mark_lost", args=[card.pk]), {})
    assert response.status_code == 400
    assert response.json()["code"] == "ERR_REASON_REQUIRED"


def test_scan_operator_cannot_manage_cards(client, user_factory, card):
    call_command("seed_roles", verbosity=0)
    user_factory(username="op", role=Role.SCAN_OPERATOR, password=PASSWORD)
    client.login(username="op", password=PASSWORD)
    assert client.get(reverse("cards_api:cards")).status_code == 403
    assert (
        _post(client, reverse("cards_api:mark_lost", args=[card.pk]), {"reason": "x"}).status_code
        == 403
    )


def test_cards_page_renders(admin_client_):
    assert admin_client_.get(reverse("cards:list")).status_code == 200


# ------------------------------------------------------ creating stock (UI) #


def test_generate_endpoint_creates_available_stock(admin_client_):
    response = _post(admin_client_, reverse("cards_api:generate"), {"count": 25, "batch": "B9"})
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["created"] == 25
    assert data["batch"] == "B9"
    assert data["first"].startswith("CARD-")

    cards = StudentCard.objects.filter(batch="B9")
    assert cards.count() == 25
    assert cards.filter(status=CardStatus.AVAILABLE).count() == 25
    assert len({c.qr_token for c in cards}) == 25


@pytest.mark.parametrize("count", [0, -5, 999999, "many"])
def test_generate_rejects_a_bad_count(admin_client_, count):
    response = _post(admin_client_, reverse("cards_api:generate"), {"count": count})
    assert response.status_code == 400
    assert "count" in response.json()["field_errors"]
    assert StudentCard.objects.count() == 0


def test_generation_is_audited(admin_client_):
    _post(admin_client_, reverse("cards_api:generate"), {"count": 3, "batch": "B1"})
    entry = AuditLog.objects.filter(action=AuditAction.CARD_IMPORTED).latest("created_at")
    assert entry.changes["source"] == "generated"
    assert entry.changes["count"] == 3
    assert entry.actor.username == "boss"


def test_import_endpoint_accepts_a_csv_upload(admin_client_):
    from io import BytesIO

    payload = BytesIO(b"card_number,qr_token\nCARD-900,CMS1:tok900\nCARD-901,CMS1:tok901\n")
    payload.name = "batch.csv"
    response = admin_client_.post(reverse("cards_api:import_csv"), {"file": payload, "batch": "V1"})
    assert response.status_code == 200
    assert response.json()["data"]["created"] == 2
    assert StudentCard.objects.filter(batch="V1").count() == 2


def test_import_endpoint_reports_the_offending_row(admin_client_):
    from io import BytesIO

    payload = BytesIO(b"card_number,qr_token\nCARD-902,CMS1:tok902\nCARD-902,CMS1:tok903\n")
    payload.name = "batch.csv"
    response = admin_client_.post(reverse("cards_api:import_csv"), {"file": payload})

    assert response.status_code == 400
    assert "CARD-902" in response.json()["field_errors"]["file"][0]
    assert StudentCard.objects.count() == 0  # all-or-nothing


def test_import_without_a_file_is_a_field_error(admin_client_):
    response = admin_client_.post(reverse("cards_api:import_csv"), {})
    assert response.status_code == 400
    assert "file" in response.json()["field_errors"]


def test_print_sheet_is_a_pdf_of_qr_codes(admin_client_):
    _post(admin_client_, reverse("cards_api:generate"), {"count": 4, "batch": "B2"})
    response = admin_client_.get(reverse("cards_api:export_batch") + "?batch=B2&format=pdf")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response.content.startswith(b"%PDF-")
    assert len(response.content) > 6000  # four QR images plus the page furniture


def test_csv_export_carries_live_tokens_and_needs_the_manage_permission(
    admin_client_, client, user_factory
):
    _post(admin_client_, reverse("cards_api:generate"), {"count": 2, "batch": "B3"})
    card = StudentCard.objects.filter(batch="B3").first()

    allowed = admin_client_.get(reverse("cards_api:export_batch") + "?batch=B3&format=csv")
    assert allowed.status_code == 200
    assert card.qr_token in allowed.content.decode("utf-8")

    user_factory(username="viewer", role=Role.INSTRUCTOR, password=PASSWORD)
    client.login(username="viewer", password=PASSWORD)
    assert client.get(reverse("cards_api:export_batch") + "?format=csv").status_code == 403


def test_only_card_managers_can_create_stock(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="cash5", role=Role.CASHIER, password=PASSWORD)
    client.login(username="cash5", password=PASSWORD)

    assert _post(client, reverse("cards_api:generate"), {"count": 5}).status_code == 403
    assert StudentCard.objects.count() == 0


def test_the_command_and_the_button_share_one_implementation():
    """Both call importer.generate_batch — no second copy of the rules."""
    import inspect

    from apps.cards.management.commands import generate_cards as command

    assert "generate_batch" in inspect.getsource(command)


# ------------------------------------------------ issuing a card to a student #


def test_available_feeder_lists_only_free_stock(admin_client_, card, spare_card, student):
    services.assign_card(card, student)

    data = admin_client_.get(reverse("cards_api:available")).json()["data"]
    assert data["count"] == 1
    assert [row["card_number"] for row in data["results"]] == [spare_card.card_number]


def test_issue_takes_the_next_card_from_stock(admin_client_, card, spare_card, student):
    response = _post(admin_client_, reverse("cards_api:issue"), {"student_id": student.pk})
    assert response.status_code == 200

    data = response.json()["data"]
    assert data["card"]["card_number"] == card.card_number  # lowest number first
    assert data["print_url"] == f"/api/cards/{card.pk}/print/"

    card.refresh_from_db()
    assert card.status == CardStatus.ASSIGNED
    assert card.current_student_id == student.pk
    assert CardAssignment.objects.filter(card=card, released_at__isnull=True).exists()


def test_issue_can_target_a_specific_card(admin_client_, card, spare_card, student):
    response = _post(
        admin_client_,
        reverse("cards_api:issue"),
        {"student_id": student.pk, "card_id": spare_card.pk},
    )
    assert response.json()["data"]["card"]["card_number"] == spare_card.card_number
    card.refresh_from_db()
    assert card.status == CardStatus.AVAILABLE


def test_issue_with_empty_stock_mints_a_card_when_asked(admin_client_, student):
    assert StudentCard.objects.available().count() == 0

    refused = _post(admin_client_, reverse("cards_api:issue"), {"student_id": student.pk})
    assert refused.status_code == 409
    assert refused.json()["code"] == "ERR_NO_STOCK"

    minted = _post(
        admin_client_,
        reverse("cards_api:issue"),
        {"student_id": student.pk, "mint": True, "batch": "ONDEMAND"},
    )
    assert minted.status_code == 200
    card = StudentCard.objects.get(current_student=student)
    assert card.status == CardStatus.ASSIGNED
    assert card.batch == "ONDEMAND"


def test_issue_refuses_a_student_who_already_holds_a_card(admin_client_, card, spare_card, student):
    services.assign_card(card, student)
    response = _post(admin_client_, reverse("cards_api:issue"), {"student_id": student.pk})
    assert response.status_code == 409
    assert response.json()["code"] == "ERR_STUDENT_HAS_CARD"


def test_printable_card_is_id1_sized_and_carries_the_qr(admin_client_, card, student):
    import io

    from pypdf import PdfReader

    services.assign_card(card, student)
    response = admin_client_.get(reverse("cards_api:print_card", args=[card.pk]))

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert student.student_code in response["Content-Disposition"]

    page = PdfReader(io.BytesIO(response.content)).pages[0]
    width_mm = round(float(page.mediabox.width) / 72 * 25.4, 1)
    height_mm = round(float(page.mediabox.height) / 72 * 25.4, 1)
    assert (width_mm, height_mm) == (85.6, 54.0)  # ID-1
    assert len(page.images) == 1  # the QR


def test_the_card_face_carries_no_contact_details(admin_client_, card, student):
    import io

    from pypdf import PdfReader

    student.guardian_phone = "01099998888"
    student.address = "شارع الهرم"
    student.save()
    services.assign_card(card, student)

    response = admin_client_.get(reverse("cards_api:print_card", args=[card.pk]))
    text = PdfReader(io.BytesIO(response.content)).pages[0].extract_text(extraction_mode="layout")

    assert student.student_code in text
    assert "01099998888" not in text
    assert card.qr_token not in text  # the token is in the QR, not printed as text


def test_only_card_managers_can_issue(client, user_factory, card, student):
    call_command("seed_roles", verbosity=0)
    user_factory(username="teach9", role=Role.INSTRUCTOR, password=PASSWORD)
    client.login(username="teach9", password=PASSWORD)
    response = _post(client, reverse("cards_api:issue"), {"student_id": student.pk})
    assert response.status_code == 403
