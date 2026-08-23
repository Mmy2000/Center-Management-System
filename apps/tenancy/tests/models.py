"""Throwaway models that exist only to test ``TenantOwnedModel`` itself.

Kept in their own module, imported by ``apps/tenancy/conftest.py``, and given
real tables for the whole test session. That combination is deliberate:

* A model declared inside a test function or an ``isolate_apps`` block cannot
  carry a ``ForeignKey`` to ``tenancy.Tenant`` — the isolated registry does not
  contain it.
* A model declared at import time with a table that only exists for one module
  is worse: everything that sweeps ``apps.get_models()`` for tenant-owned models
  (the export, the purge command, the leak suite) would find a model with no
  table and fail in whichever test ran next.

So: registered once, table present for the whole session, empty unless a test
puts something in it. The sweeps find them and are satisfied.
"""

from django.db import models

from apps.tenancy.base import TenantOwnedModel


class Widget(TenantOwnedModel):
    """A minimal tenant-owned model."""

    name = models.CharField(max_length=50)

    class Meta:
        app_label = "tenancy"


class Gadget(TenantOwnedModel):
    """Carries a FK to another tenant-owned model, so the cross-tenant FK guard
    has something to guard. Declares its own Meta on purpose — a subclass must
    not have to remember any manager plumbing."""

    widget = models.ForeignKey(Widget, on_delete=models.CASCADE, related_name="gadgets")
    label = models.CharField(max_length=50)

    class Meta:
        app_label = "tenancy"
        verbose_name = "gadget"
