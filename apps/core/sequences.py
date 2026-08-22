"""Sequential code allocation (TASK-021, TASK-061)."""

from django.db import transaction

from .models import Sequence


def next_number(key: str) -> int:
    """Reserve and return the next integer for ``key``.

    Must be called inside a transaction; the row lock serialises concurrent
    callers (a no-op on SQLite, real on PostgreSQL — see docs/04 §F.3).
    """
    with transaction.atomic():
        sequence, _created = Sequence.objects.get_or_create(key=key)
        sequence = Sequence.objects.select_for_update().get(pk=sequence.pk)
        value = sequence.next_value
        sequence.next_value = value + 1
        sequence.save(update_fields=["next_value", "updated_at"])
        return value


def format_code(prefix: str, value: int, *, width: int = 6) -> str:
    return f"{prefix}{value:0{width}d}"
