"""The current tenant, as a context variable (docs/10 §N.4, TASK-091).

    from apps.tenancy.context import current_tenant, tenant_context

    current_tenant()              # -> Tenant | None
    with tenant_context(tenant):  # -> everything inside is scoped to it
        Student.objects.count()

A ``ContextVar`` rather than a thread-local because it is correct under ASGI as
well as under threaded WSGI workers: each coroutine and each thread gets its own
value, and ``reset(token)`` restores exactly the previous one rather than
guessing.

The single rule that makes this safe: **every setter pairs with a reset in a
``finally``**. A leaked value means the next request served by that worker reads
another center's data — the highest-severity bug class in this design, and the
reason :func:`tenant_context` is a context manager and never two loose calls.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from .exceptions import TenantContextRequired

if TYPE_CHECKING:  # pragma: no cover - import cycle: models imports this module
    from .models import Tenant

_tenant: ContextVar["Tenant | None"] = ContextVar("current_tenant", default=None)

# Set while console code deliberately reads across every tenant. Nothing keys
# off it yet; it exists so that "no tenant" (a bug) and "all tenants" (a
# decision) are distinguishable in a traceback and in a log line.
_all_tenants: ContextVar[bool] = ContextVar("all_tenants", default=False)


def current_tenant() -> "Tenant | None":
    """The tenant this request/task belongs to, or ``None`` outside one."""
    return _tenant.get()


def require_tenant() -> "Tenant":
    """Like :func:`current_tenant`, but a missing tenant is an error."""
    tenant = _tenant.get()
    if tenant is None:
        raise TenantContextRequired(
            "No tenant in context. Wrap the call in tenant_context(tenant), or "
            "use Model.all_tenants if crossing tenants is genuinely intended."
        )
    return tenant


def current_tenant_id() -> int | None:
    """The primary key alone — avoids touching a deferred Tenant instance."""
    tenant = _tenant.get()
    return None if tenant is None else tenant.pk


def is_all_tenants() -> bool:
    return _all_tenants.get()


def set_tenant(tenant: "Tenant | None"):
    """Low-level setter. Returns the token; the caller **must** reset it.

    Only middleware should call this directly — everything else uses
    :func:`tenant_context`, which cannot forget the reset.
    """
    return _tenant.set(tenant)


def reset_tenant(token) -> None:
    _tenant.reset(token)


@contextmanager
def tenant_context(tenant: "Tenant | None"):
    """Run a block as ``tenant``. Re-entrant; restores the previous value.

    The restore happens on the way out of *any* exit — normal, ``return`` or
    exception — which is what makes nesting safe.
    """
    token = _tenant.set(tenant)
    try:
        yield tenant
    finally:
        _tenant.reset(token)


@contextmanager
def all_tenants_context():
    """Mark a block as deliberately crossing tenants (console, commands).

    This does **not** disable the managers — ``Model.objects`` still requires a
    tenant. Reaching across tenants is always an explicit ``Model.all_tenants``.
    This marker exists so that intent is visible in code review and in logs.
    """
    token = _all_tenants.set(True)
    try:
        yield
    finally:
        _all_tenants.reset(token)
