"""The abstract base every domain model shares.

Lives in its own module, not in ``core.models``, so that
``apps.tenancy.base.TenantOwnedModel`` can extend it without
``core.models`` <-> ``tenancy.models`` becoming an import cycle:
``core.models`` needs ``TenantOwnedModel`` (Setting, Sequence), and
``TenantOwnedModel`` needs ``TimeStampedModel``. This module imports neither.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    """Base for every domain model."""

    created_at = models.DateTimeField(_("أُنشئ في"), auto_now_add=True)
    updated_at = models.DateTimeField(_("عُدّل في"), auto_now=True)

    class Meta:
        abstract = True
