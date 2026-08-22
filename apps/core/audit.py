"""Audit service (TASK-005).

Every sensitive mutation calls :func:`record`. The actor/IP come from a
thread-local populated by :class:`apps.core.middleware.AuditContextMiddleware`,
so services never need the request object — management commands simply record
``actor=None``.
"""

import datetime
import threading
import uuid
from decimal import Decimal
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models

_ctx = threading.local()


# --------------------------------------------------------------------------- #
# Request context
# --------------------------------------------------------------------------- #


def set_context(*, user=None, ip=None, user_agent="") -> None:
    _ctx.user = user if (user is not None and getattr(user, "is_authenticated", False)) else None
    _ctx.ip = ip
    _ctx.user_agent = (user_agent or "")[:300]


def clear_context() -> None:
    _ctx.user = None
    _ctx.ip = None
    _ctx.user_agent = ""


def current_actor():
    return getattr(_ctx, "user", None)


def current_ip():
    return getattr(_ctx, "ip", None)


def current_user_agent() -> str:
    return getattr(_ctx, "user_agent", "") or ""


def client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


# --------------------------------------------------------------------------- #
# Diffing
# --------------------------------------------------------------------------- #


def jsonable(value: Any) -> Any:
    """Coerce a model field value into something JSONField can store."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, models.Model):
        return {"id": value.pk, "repr": str(value)}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    return str(value)


def snapshot(instance, fields: list[str]) -> dict[str, Any]:
    """Capture the current values of ``fields`` on ``instance``."""
    if instance is None:
        return {}
    data = {}
    for name in fields:
        data[name] = jsonable(getattr(instance, name, None))
    return data


def diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return ``{field: {"old": ..., "new": ...}}`` for changed fields only."""
    changes = {}
    for key in set(before) | set(after):
        old, new = before.get(key), after.get(key)
        if old != new:
            changes[key] = {"old": old, "new": new}
    return changes


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #


def record(
    action: str,
    instance=None,
    *,
    changes: dict | None = None,
    reason: str = "",
    actor=None,
    object_repr: str | None = None,
):
    """Write one AuditLog row. Never raises into the caller's transaction path
    for anything other than a genuine database error."""
    from .models import AuditLog

    content_type = None
    object_id = None
    if instance is not None and getattr(instance, "pk", None) is not None:
        content_type = ContentType.objects.get_for_model(instance.__class__)
        object_id = instance.pk

    from apps.tenancy.context import current_tenant

    return AuditLog.objects.create(
        # Read from context, never passed in: a management command with no
        # tenant writes a null one rather than crashing (docs/10 §N.2).
        tenant=current_tenant(),
        actor=actor if actor is not None else current_actor(),
        action=action,
        content_type=content_type,
        object_id=object_id,
        object_repr=(object_repr if object_repr is not None else str(instance or ""))[:200],
        changes=jsonable(changes or {}),
        reason=reason or "",
        ip_address=current_ip(),
        user_agent=current_user_agent(),
    )
