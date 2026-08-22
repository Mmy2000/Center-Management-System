"""QR token generation, normalisation and masking (docs/03 §E.2).

The token identifies a card; it does not authorize anything on its own. A scan
is only possible from an authenticated operator session against an open lesson,
which is why a random-and-indexed token beats a signed one: a signature would
still need the same database lookup to learn the card's status and holder.
"""

import re
import secrets

from django.utils.translation import gettext as _

from apps.core.http import DomainError
from apps.core.registry import settings_registry

MAX_TOKEN_LENGTH = 64
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9:_\-]+$")


def generate_token(prefix: str | None = None) -> str:
    """``CMS1:`` + 22 URL-safe base62 chars ≈ 128 bits of entropy."""
    if prefix is None:
        prefix = settings_registry.get("cards.token_prefix")
    body = secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:22]
    while len(body) < 22:  # extremely rare: refill after stripping separators
        body += secrets.token_urlsafe(8).replace("-", "").replace("_", "")
        body = body[:22]
    return f"{prefix}:{body}" if prefix else body


def normalize_token(raw: str) -> str:
    """Clean a scanned payload before it ever reaches the database.

    HID scanners append CR/LF; operators paste stray whitespace. Anything
    longer than 64 chars or containing unexpected characters is rejected here,
    so a malformed scan costs zero queries.
    """
    token = (raw or "").strip().strip("\r\n").strip()
    if not token:
        raise DomainError(
            "ERR_CARD_NOT_FOUND",
            _("لم يتم قراءة رمز البطاقة"),
            status=400,
        )
    if len(token) > MAX_TOKEN_LENGTH or not TOKEN_PATTERN.match(token):
        raise DomainError(
            "ERR_CARD_NOT_FOUND",
            _("رمز البطاقة غير صالح"),
            status=400,
        )
    if ":" not in token and not settings_registry.get("cards.accept_unprefixed_tokens"):
        raise DomainError(
            "ERR_CARD_NOT_FOUND",
            _("رمز البطاقة غير صالح"),
            status=400,
        )
    return token


def mask_token(token: str) -> str:
    """Never show a full token in the UI, logs or exports."""
    if not token:
        return ""
    if len(token) <= 10:
        return token[:3] + "…"
    return f"{token[:7]}…{token[-3:]}"
