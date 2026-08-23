"""Card batch creation — shared by the management commands and the UI.

Two ways stock enters the system (docs/03 §E.1):
  * a vendor already printed the cards → import their CSV;
  * the cards are not printed yet → generate tokens here, then export a sheet
    for the print shop.

Both paths are all-or-nothing: one bad row aborts the whole batch, because a
half-imported batch is worse than none.
"""

import csv
import io

from django.db import transaction
from django.utils.translation import gettext as _

from apps.core.audit import record
from apps.core.http import DomainError
from apps.core.models import AuditAction
from apps.core.sequences import format_code, next_number
from apps.tenancy import quota

from .models import StudentCard
from .tokens import MAX_TOKEN_LENGTH, TOKEN_PATTERN, generate_token

CARD_NUMBER_KEY = "card_number"
CARD_NUMBER_PREFIX = "CARD-"
MAX_BATCH = 20000
REQUIRED_COLUMNS = {"card_number", "qr_token"}


class ImportError_(DomainError):
    """A row-level problem, reported with the row number the operator sees."""

    def __init__(self, message, *, row=None):
        text = _("سطر %(row)s: %(msg)s") % {"row": row, "msg": message} if row else message
        super().__init__("ERR_IMPORT", text, status=400, field_errors={"file": [text]})


def read_rows(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportError_(_("الملف فارغ"))
    headers = {(name or "").strip().lower() for name in reader.fieldnames}
    if not REQUIRED_COLUMNS <= headers:
        raise ImportError_(_("الملف يجب أن يحتوي على عمودي card_number و qr_token"))
    return [
        {(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
        for row in reader
    ]


def validate_rows(rows: list[dict], *, default_batch: str = "") -> list[StudentCard]:
    seen_numbers, seen_tokens = set(), set()
    cards = []

    for index, row in enumerate(rows, start=2):  # row 1 is the header
        number, token = row.get("card_number", ""), row.get("qr_token", "")
        if not number or not token:
            raise ImportError_(_("رقم البطاقة والرمز مطلوبان"), row=index)
        if len(token) > MAX_TOKEN_LENGTH or not TOKEN_PATTERN.match(token):
            raise ImportError_(_("صيغة الرمز غير صحيحة"), row=index)
        if number in seen_numbers:
            raise ImportError_(_("رقم بطاقة مكرر داخل الملف: %(n)s") % {"n": number}, row=index)
        if token in seen_tokens:
            raise ImportError_(_("رمز مكرر داخل الملف"), row=index)

        seen_numbers.add(number)
        seen_tokens.add(token)
        cards.append(
            StudentCard(card_number=number, qr_token=token, batch=row.get("batch") or default_batch)
        )

    clashing = set(
        StudentCard.objects.filter(card_number__in=seen_numbers).values_list(
            "card_number", flat=True
        )
    )
    if clashing:
        raise ImportError_(_("أرقام موجودة بالفعل: %(n)s") % {"n": ", ".join(sorted(clashing)[:5])})
    if StudentCard.objects.filter(qr_token__in=seen_tokens).exists():
        raise ImportError_(_("أحد الرموز مستخدم بالفعل في النظام"))

    return cards


@transaction.atomic
def import_batch(text: str, *, batch: str = "", actor=None) -> dict:
    cards = validate_rows(read_rows(text), default_batch=batch)
    # Before the insert, and for the *whole* batch: a partial import would leave
    # the print shop's sheet and the database disagreeing about which numbers
    # exist, which is far worse than a refusal (docs/10 §N.9).
    quota.check("cards", len(cards))
    StudentCard.objects.bulk_create(cards, batch_size=500)

    record(
        AuditAction.CARD_IMPORTED,
        changes={"count": len(cards), "batch": batch, "source": "csv"},
        reason=_("استيراد دفعة بطاقات"),
        object_repr=f"{len(cards)} cards",
        actor=actor,
    )
    return {
        "created": len(cards),
        "batch": batch,
        "first": cards[0].card_number if cards else None,
        "last": cards[-1].card_number if cards else None,
    }


@transaction.atomic
def generate_batch(count: int, *, batch: str = "", actor=None) -> dict:
    """Mint `count` AVAILABLE cards with fresh, non-guessable tokens."""
    quota.check("cards", count if isinstance(count, int) and count > 0 else 1)
    if not isinstance(count, int) or count < 1 or count > MAX_BATCH:
        raise DomainError(
            "ERR_VALIDATION",
            _("العدد يجب أن يكون بين 1 و %(max)s") % {"max": MAX_BATCH},
            field_errors={"count": [_("عدد غير صحيح")]},
        )

    tokens: set[str] = set()
    while len(tokens) < count:
        tokens.add(generate_token())

    cards = [
        StudentCard(
            card_number=format_code(CARD_NUMBER_PREFIX, next_number(CARD_NUMBER_KEY)),
            qr_token=token,
            batch=batch,
        )
        for token in tokens
    ]
    StudentCard.objects.bulk_create(cards, batch_size=500)

    record(
        AuditAction.CARD_IMPORTED,
        changes={"count": count, "batch": batch, "source": "generated"},
        reason=_("توليد دفعة بطاقات"),
        object_repr=f"{count} cards",
        actor=actor,
    )
    return {
        "created": count,
        "batch": batch,
        "first": cards[0].card_number,
        "last": cards[-1].card_number,
    }
