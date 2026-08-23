"""What platform staff did to which client (docs/10 §N.10, TASK-118).

Mirrors :func:`apps.core.audit.record` deliberately, so both trails read the
same at the call site — but they are separate tables on purpose. ``core.AuditLog``
is the *center's* trail and a center admin can read it; this one is yours, lives
on the console host, and no tenant user can reach it.
"""

from .models import PlatformAuditLog


def record(action, *, tenant=None, actor=None, reason="", changes=None, object_repr=""):
    """Write one platform audit row. Never raises into the caller's path."""
    from apps.core.audit import current_actor, current_ip, current_user_agent, jsonable

    return PlatformAuditLog.objects.create(
        actor=actor if actor is not None else current_actor(),
        tenant=tenant,
        action=action,
        object_repr=(object_repr or (str(tenant) if tenant else ""))[:200],
        changes=jsonable(changes or {}),
        reason=reason or "",
        ip_address=current_ip(),
        user_agent=current_user_agent(),
    )
