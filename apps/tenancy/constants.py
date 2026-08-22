"""Tenancy enums and the reserved-slug list (docs/10 §N.2, TASK-089)."""

from django.db import models
from django.utils.translation import gettext_lazy as _

# A slug is the DNS label of the center's subdomain, so it obeys hostname rules:
# lowercase alphanumerics and hyphens, never leading or trailing a hyphen.
SLUG_PATTERN = r"^[a-z0-9]([a-z0-9-]{1,30}[a-z0-9])$"

# Hostnames the platform needs for itself. A center that took one of these would
# shadow the console, the API docs or the mail record — and the mistake is only
# discoverable after DNS has propagated, so it is refused at creation instead.
RESERVED_SLUGS = frozenset(
    {
        "admin",
        "api",
        "app",
        "assets",
        "billing",
        "cdn",
        "console",
        "dashboard",
        "dev",
        "docs",
        "ftp",
        "help",
        "localhost",
        "mail",
        "media",
        "ns1",
        "ns2",
        "platform",
        "smtp",
        "staging",
        "static",
        "status",
        "support",
        "test",
        "www",
    }
)


class TenantStatus(models.TextChoices):
    """The lifecycle of a client (docs/10 §N.2).

    Only ``Tenant.transition_to()`` writes this field, so every change is
    audited in exactly one place.
    """

    TRIAL = "TRIAL", _("تجريبي")
    ACTIVE = "ACTIVE", _("نشط")
    PAST_DUE = "PAST_DUE", _("متأخر السداد")
    SUSPENDED = "SUSPENDED", _("موقوف")
    ARCHIVED = "ARCHIVED", _("مؤرشف")


#: Statuses whose users may log in and work. ``PAST_DUE`` is deliberately here:
#: a late invoice is a conversation, not a reason to stop a center from taking
#: attendance that morning.
OPERATIONAL_STATUSES = frozenset({TenantStatus.TRIAL, TenantStatus.ACTIVE, TenantStatus.PAST_DUE})


class FeatureState(models.TextChoices):
    """A per-tenant override of what the plan says (docs/10 §N.8)."""

    INHERIT = "INHERIT", _("حسب الباقة")
    ON = "ON", _("مُفعّل")
    OFF = "OFF", _("مُعطّل")


class PlatformAction(models.TextChoices):
    """Everything platform staff can do *to* a client (docs/10 §N.10)."""

    TENANT_CREATED = "TENANT_CREATED", _("إنشاء عميل")
    TENANT_UPDATED = "TENANT_UPDATED", _("تعديل عميل")
    TENANT_SUSPENDED = "TENANT_SUSPENDED", _("إيقاف عميل")
    TENANT_RESUMED = "TENANT_RESUMED", _("إعادة تفعيل عميل")
    TENANT_ARCHIVED = "TENANT_ARCHIVED", _("أرشفة عميل")
    TENANT_PURGED = "TENANT_PURGED", _("حذف بيانات عميل")
    TENANT_EXPORTED = "TENANT_EXPORTED", _("تصدير بيانات عميل")
    PLAN_CHANGED = "PLAN_CHANGED", _("تغيير الباقة")
    FEATURE_CHANGED = "FEATURE_CHANGED", _("تغيير خاصية")
    LIMIT_CHANGED = "LIMIT_CHANGED", _("تغيير حد")
    DOMAIN_ADDED = "DOMAIN_ADDED", _("إضافة نطاق")
    DOMAIN_REMOVED = "DOMAIN_REMOVED", _("حذف نطاق")
    IMPERSONATION_STARTED = "IMPERSONATION_STARTED", _("بدء الدخول نيابةً")
    IMPERSONATION_ENDED = "IMPERSONATION_ENDED", _("إنهاء الدخول نيابةً")
    SUBSCRIPTION_TRANSITION = "SUBSCRIPTION_TRANSITION", _("تغيير حالة الاشتراك")
