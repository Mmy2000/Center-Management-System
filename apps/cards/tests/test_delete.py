"""Deleting a card — only ever stock that was never used.

Principle 5 of this project is that history is immutable (docs/README). A card
that has been held by a student, or has appeared in a scan, is history: deleting
it would strand the assignment rows pointing at it and blank the card column on
every event it produced, quietly rewriting what happened. `disable_card` is the
operation for those.

What is left is genuine stock management — a duplicate import, a mis-typed
batch, a test card — and that is what this deletes.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.cards import services
from apps.cards.models import CardStatus, StudentCard
from apps.core.http import DomainError
from apps.core.models import AuditAction, AuditLog

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def card():
    return StudentCard.objects.create(
        card_number="C000001", qr_token="token-1", status=CardStatus.AVAILABLE, batch="B1"
    )


@pytest.fixture
def student(db):
    from apps.academics.models import EducationalStage, Grade
    from apps.students.services import create_student

    stage = EducationalStage.objects.create(name="S", code="S", order=1)
    grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
    return create_student(full_name="طالب", grade=grade)


# --------------------------------------------------------------- the service


def test_unused_stock_can_be_deleted(card):
    result = services.delete_card(card, reason="دفعة مكررة")
    assert result["card_number"] == "C000001"
    assert not StudentCard.objects.filter(pk=card.pk).exists()


def test_the_deletion_is_audited_before_the_row_goes(card):
    """Recorded first, or the entry points at a row that no longer exists."""
    services.delete_card(card, reason="رقم خاطئ")
    entry = AuditLog.objects.filter(action=AuditAction.CARD_DELETED).first()
    assert entry is not None
    assert entry.changes["card_number"] == "C000001"
    assert entry.reason == "رقم خاطئ"


def test_a_reason_is_required(card):
    with pytest.raises(DomainError) as exc:
        services.delete_card(card, reason="")
    assert exc.value.code == "ERR_REASON_REQUIRED"
    assert StudentCard.objects.filter(pk=card.pk).exists()


def test_an_assigned_card_is_refused(card, student):
    services.assign_card(card, student)
    with pytest.raises(DomainError) as exc:
        services.delete_card(card, reason="tidy up")
    assert exc.value.code == "ERR_CARD_IN_USE"
    assert StudentCard.objects.filter(pk=card.pk).exists()


def test_a_card_with_past_history_is_refused(card, student):
    """Released, so it is AVAILABLE again — but it was somebody's card, and the
    assignment row still says so."""
    services.assign_card(card, student)
    services.mark_lost(card, reason="فقدت")
    card.refresh_from_db()

    with pytest.raises(DomainError) as exc:
        services.delete_card(card, reason="tidy up")
    assert exc.value.code == "ERR_CARD_HAS_HISTORY"
    assert card.assignment_history.exists()


def test_a_scanned_card_is_refused(card):
    """No assignment, but it appears in the forensic log — deleting it would
    blank the card column on a scan that really happened."""
    from apps.academics.models import EducationalStage, Grade, GradeSubject, Group, Subject
    from apps.attendance.models import AttendanceEvent
    from apps.lessons.models import Lesson

    stage = EducationalStage.objects.create(name="S", code="S", order=1)
    grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
    subject = Subject.objects.create(name="P", code="P")
    offering = GradeSubject.objects.create(grade=grade, subject=subject)
    group = Group.objects.create(grade_subject=offering, name="A", code="A")
    start = timezone.now()
    lesson = Lesson.objects.create(
        group=group,
        scheduled_start=start,
        scheduled_end=start + timezone.timedelta(hours=1),
        check_in_opens_at=start,
        check_in_closes_at=start + timezone.timedelta(hours=1),
        late_after=start,
    )
    AttendanceEvent.objects.create(
        lesson=lesson, card=card, event_type="DENIED", result_code="ERR_UNKNOWN"
    )

    with pytest.raises(DomainError) as exc:
        services.delete_card(card, reason="tidy up")
    assert exc.value.code == "ERR_CARD_HAS_HISTORY"


def test_a_card_that_stood_in_for_another_is_refused(card):
    """`replaced_by` on the old card makes the new one part of a chain.

    The state is built directly rather than through `replace_card`, which would
    also assign the replacement to a student and trip the in-use guard first —
    this has to prove the chain guard itself, not the one before it.
    """
    replacement = StudentCard.objects.create(
        card_number="C000002", qr_token="token-2", status=CardStatus.AVAILABLE
    )
    card.replaced_by = replacement
    card.save(update_fields=["replaced_by"])

    assert replacement.replaces.exists(), "the replacement should know what it stood in for"

    with pytest.raises(DomainError) as exc:
        services.delete_card(replacement, reason="tidy up")
    assert exc.value.code == "ERR_CARD_HAS_HISTORY"


def test_deleting_needs_the_cards_feature(card):
    """Layer 4: a shell or a script does not pass through a view."""
    from apps.console import services as console_services
    from apps.tenancy.constants import FeatureState
    from apps.tenancy.context import current_tenant

    console_services.set_feature(current_tenant(), "cards", FeatureState.OFF)
    with pytest.raises(DomainError) as exc:
        services.delete_card(card, reason="tidy up")
    assert exc.value.code == "ERR_FEATURE_DISABLED"


# -------------------------------------------------------------- the endpoint


@pytest.fixture
def admin(client):
    from django.core.management import call_command

    from apps.accounts.services import sync_user_group

    call_command("seed_roles", verbosity=0)
    user = User.objects.create_user(username="boss", password=PASSWORD, role=Role.CENTER_ADMIN)
    sync_user_group(user)
    assert client.login(username="boss", password=PASSWORD)
    return client


def test_the_endpoint_deletes_unused_stock(admin, card):
    response = admin.delete(
        reverse("cards_api:delete", args=[card.pk]),
        data={"reason": "دفعة مكررة"},
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json()["data"]["code"] == "OK_CARD_DELETED"
    assert not StudentCard.objects.filter(pk=card.pk).exists()


def test_the_endpoint_refuses_a_used_card(admin, card, student):
    services.assign_card(card, student)
    response = admin.delete(
        reverse("cards_api:delete", args=[card.pk]),
        data={"reason": "tidy up"},
        content_type="application/json",
    )
    assert response.status_code == 409
    assert response.json()["code"] == "ERR_CARD_IN_USE"


def test_a_cashier_cannot_delete_stock(client, card):
    """`delete_studentcard` is a center-admin permission, not everyone's."""
    from django.core.management import call_command

    from apps.accounts.services import sync_user_group

    call_command("seed_roles", verbosity=0)
    user = User.objects.create_user(username="cash", password=PASSWORD, role=Role.CASHIER)
    sync_user_group(user)
    assert client.login(username="cash", password=PASSWORD)

    response = client.delete(
        reverse("cards_api:delete", args=[card.pk]),
        data={"reason": "tidy up"},
        content_type="application/json",
    )
    assert response.status_code == 403
    assert StudentCard.objects.filter(pk=card.pk).exists()
