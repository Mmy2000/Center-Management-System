"""Card lifecycle services (TASK-030 → 033).

Every state change is one transaction that locks the card, re-verifies its
status inside the lock (TOCTOU guard), moves the history row and writes an
audit entry. The partial unique indexes are the second line of defence.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.audit import record
from apps.core.http import DomainError
from apps.core.models import AuditAction
from apps.tenancy.guards import require_feature

from .models import CardAssignment, CardStatus, ReleaseReason, StudentCard
from .tokens import normalize_token

# Distinct codes so the scanner can say exactly what is wrong (docs/03 §D.6).
STATUS_ERRORS = {
    CardStatus.AVAILABLE: ("ERR_CARD_UNASSIGNED", _("هذه البطاقة غير مرتبطة بطالب")),
    CardStatus.LOST: ("ERR_CARD_LOST", _("هذه البطاقة مبلّغ عن فقدها")),
    CardStatus.DISABLED: ("ERR_CARD_DISABLED", _("هذه البطاقة معطّلة")),
    CardStatus.REPLACED: ("ERR_CARD_REPLACED", _("هذه البطاقة تم استبدالها")),
}


def find_card(raw_token: str) -> StudentCard:
    """Resolve a scanned payload to a card. One indexed query."""
    token = normalize_token(raw_token)
    card = (
        StudentCard.objects.select_related("current_student__grade__stage")
        .filter(qr_token=token)
        .first()
    )
    if card is None:
        raise DomainError("ERR_CARD_NOT_FOUND", _("بطاقة غير معروفة"), status=404)
    return card


def assert_usable(card: StudentCard) -> StudentCard:
    """Gate 2 of the scan path."""
    if card.status != CardStatus.ASSIGNED:
        code, message = STATUS_ERRORS[card.status]
        raise DomainError(code, message, status=409, data={"card_number": card.card_number})
    return card


@transaction.atomic
def assign_card(card: StudentCard, student, *, actor=None, notes: str = "") -> StudentCard:
    require_feature("cards")
    card = StudentCard.objects.select_for_update().get(pk=card.pk)
    if card.status != CardStatus.AVAILABLE:
        code, message = (
            ("ERR_CARD_ALREADY_ASSIGNED", _("البطاقة مخصصة لطالب آخر"))
            if card.status == CardStatus.ASSIGNED
            else STATUS_ERRORS[card.status]
        )
        raise DomainError(code, message, status=409, data={"card_number": card.card_number})

    if StudentCard.objects.assigned().filter(current_student=student).exists():
        raise DomainError(
            "ERR_STUDENT_HAS_CARD",
            _("الطالب لديه بطاقة نشطة بالفعل — استخدم الاستبدال"),
            status=409,
        )

    now = timezone.now()
    card.status = CardStatus.ASSIGNED
    card.current_student = student
    card.issued_at = card.issued_at or now
    card.save(update_fields=["status", "current_student", "issued_at", "updated_at"])

    CardAssignment.objects.create(
        card=card, student=student, assigned_at=now, assigned_by=actor, notes=notes
    )
    record(
        AuditAction.CARD_ASSIGNED,
        card,
        changes={"current_student": {"old": None, "new": str(student)}},
        reason=notes,
        actor=actor,
    )
    return card


def _release(card: StudentCard, *, reason: str, actor=None) -> None:
    """Close the open history row and detach the holder. Caller holds the lock."""
    now = timezone.now()
    open_assignment = card.assignment_history.filter(released_at__isnull=True).first()
    if open_assignment:
        open_assignment.released_at = now
        open_assignment.release_reason = reason
        open_assignment.released_by = actor
        open_assignment.save(
            update_fields=["released_at", "release_reason", "released_by", "updated_at"]
        )
    card.current_student = None


@transaction.atomic
def mark_lost(card: StudentCard, *, actor=None, reason: str = "") -> StudentCard:
    require_feature("cards")
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("السبب مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    card = StudentCard.objects.select_for_update().get(pk=card.pk)
    if card.status in (CardStatus.LOST, CardStatus.REPLACED):
        raise DomainError("ERR_CARD_LOST", _("البطاقة مسجلة كمفقودة بالفعل"), status=409)

    previous = card.status
    _release(card, reason=ReleaseReason.LOST, actor=actor)
    card.status = CardStatus.LOST
    card.lost_at = timezone.now()
    card.save(update_fields=["status", "current_student", "lost_at", "updated_at"])

    record(
        AuditAction.CARD_MARKED_LOST,
        card,
        changes={"status": {"old": previous, "new": CardStatus.LOST}},
        reason=reason,
        actor=actor,
    )
    return card


@transaction.atomic
def disable_card(card: StudentCard, *, actor=None, reason: str = "") -> StudentCard:
    require_feature("cards")
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("السبب مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    card = StudentCard.objects.select_for_update().get(pk=card.pk)
    if card.status == CardStatus.DISABLED:
        return card

    previous = card.status
    _release(card, reason=ReleaseReason.DISABLED, actor=actor)
    card.status = CardStatus.DISABLED
    card.disabled_at = timezone.now()
    card.save(update_fields=["status", "current_student", "disabled_at", "updated_at"])

    record(
        AuditAction.CARD_DISABLED,
        card,
        changes={"status": {"old": previous, "new": CardStatus.DISABLED}},
        reason=reason,
        actor=actor,
    )
    return card


@transaction.atomic
def replace_card(
    old_card: StudentCard,
    new_card: StudentCard,
    *,
    actor=None,
    reason: str = "",
    old_status: str = CardStatus.REPLACED,
):
    """Old card out, new card in, same student, history intact (docs/03 §E.4).

    Historical attendance is untouched: it references the *student*, and
    ``card_used`` keeps pointing at the old card as a historical fact.
    """
    if not reason:
        raise DomainError(
            "ERR_REASON_REQUIRED",
            _("السبب مطلوب"),
            field_errors={"reason": [_("السبب مطلوب")]},
        )
    if old_card.pk == new_card.pk:
        raise DomainError("ERR_SAME_CARD", _("لا يمكن استبدال البطاقة بنفسها"), status=409)
    if old_status not in (CardStatus.REPLACED, CardStatus.LOST, CardStatus.DISABLED):
        raise DomainError(
            "ERR_VALIDATION",
            _("حالة غير مسموح بها للبطاقة القديمة"),
            field_errors={"old_status": [_("حالة غير معروفة")]},
        )

    first, second = sorted([old_card.pk, new_card.pk])  # stable lock order
    locked = {
        card.pk: card
        for card in StudentCard.objects.select_for_update().filter(pk__in=[first, second])
    }
    old_card, new_card = locked[old_card.pk], locked[new_card.pk]

    student = old_card.current_student
    if student is None or old_card.status != CardStatus.ASSIGNED:
        raise DomainError(
            "ERR_CARD_NOT_ASSIGNED",
            _("البطاقة القديمة غير مرتبطة بطالب"),
            status=409,
        )
    if new_card.status != CardStatus.AVAILABLE:
        raise DomainError(
            "ERR_CARD_ALREADY_ASSIGNED",
            _("البطاقة الجديدة غير متاحة"),
            status=409,
            data={"card_number": new_card.card_number},
        )

    now = timezone.now()
    _release(old_card, reason=ReleaseReason.REPLACED, actor=actor)
    old_card.status = old_status
    old_card.replaced_at = now
    old_card.replaced_by = new_card
    if old_card.status == CardStatus.LOST:
        old_card.lost_at = now
    old_card.save(
        update_fields=[
            "status",
            "current_student",
            "replaced_at",
            "replaced_by",
            "lost_at",
            "updated_at",
        ]
    )
    record(
        AuditAction.CARD_REPLACED,
        old_card,
        changes={
            "status": {"old": CardStatus.ASSIGNED, "new": old_card.status},
            "replaced_by": {"old": None, "new": new_card.card_number},
        },
        reason=reason,
        actor=actor,
    )

    new_card.status = CardStatus.ASSIGNED
    new_card.current_student = student
    new_card.issued_at = new_card.issued_at or now
    new_card.save(update_fields=["status", "current_student", "issued_at", "updated_at"])
    CardAssignment.objects.create(
        card=new_card, student=student, assigned_at=now, assigned_by=actor, notes=reason
    )
    record(
        AuditAction.CARD_ASSIGNED,
        new_card,
        changes={"current_student": {"old": None, "new": str(student)}},
        reason=reason,
        actor=actor,
    )
    return old_card, new_card
