"""The control plane: who the clients are (docs/10 §N.2, TASK-089/090).

Nothing in this module is tenant-owned — this *is* the table of tenants. It is
read by middleware on every request (cached) and written only by the console.
"""

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.base import TimeStampedModel

from .base import TenantOwnedModel  # noqa: F401  (re-exported: the canonical import path)
from .constants import (
    OPERATIONAL_STATUSES,
    RESERVED_SLUGS,
    SLUG_PATTERN,
    FeatureState,
    PlatformAction,
    TenantStatus,
)
from .features import spec_for


def validate_slug(value: str):
    """A slug is a DNS label and a permanent identity — validate it as both."""
    if not re.match(SLUG_PATTERN, value or ""):
        raise ValidationError(
            _(
                "المعرّف يجب أن يتكون من حروف إنجليزية صغيرة وأرقام وشرطات، ويبدأ وينتهي بحرف أو رقم."
            )
        )
    if value in RESERVED_SLUGS:
        raise ValidationError(_("هذا المعرّف محجوز للنظام: %(v)s") % {"v": value})


def validate_feature_key(value: str):
    """Reject a key that is not in the catalogue (typo protection)."""
    try:
        spec_for(value)
    except KeyError:
        raise ValidationError(_("خاصية غير معروفة: %(v)s") % {"v": value}) from None


class Plan(TimeStampedModel):
    """A bundle of features and limits sold as one thing.

    Limits are ``NULL`` for unlimited. ``price_note`` is free text because the
    invoicing happens outside this system — the console records what was agreed,
    it does not charge anyone.
    """

    slug = models.SlugField(_("المعرّف"), max_length=40, unique=True)
    name = models.CharField(_("الاسم"), max_length=100)
    description = models.TextField(_("الوصف"), blank=True)
    is_public = models.BooleanField(_("متاح للعرض"), default=True)
    sort_order = models.PositiveSmallIntegerField(_("الترتيب"), default=0)

    max_students = models.PositiveIntegerField(_("حد الطلاب"), null=True, blank=True)
    max_users = models.PositiveIntegerField(_("حد المستخدمين"), null=True, blank=True)
    max_groups = models.PositiveIntegerField(_("حد المجموعات"), null=True, blank=True)
    max_cards = models.PositiveIntegerField(_("حد البطاقات"), null=True, blank=True)
    storage_mb = models.PositiveIntegerField(_("مساحة التخزين (ميجابايت)"), null=True, blank=True)
    retention_days = models.PositiveIntegerField(_("مدة الاحتفاظ بعد الأرشفة (يوم)"), default=90)

    price_note = models.CharField(_("ملاحظة السعر"), max_length=200, blank=True)

    class Meta:
        verbose_name = _("باقة")
        verbose_name_plural = _("الباقات")
        ordering = ["sort_order", "slug"]

    def __str__(self):
        return self.name

    @property
    def feature_keys(self) -> frozenset[str]:
        return frozenset(self.features.values_list("feature_key", flat=True))

    def limit(self, resource: str):
        """``max_<resource>`` or ``None`` for unlimited."""
        return getattr(self, f"max_{resource}", None)


class PlanFeature(models.Model):
    """A feature this plan turns on by default."""

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="features")
    feature_key = models.CharField(_("الخاصية"), max_length=60, validators=[validate_feature_key])

    class Meta:
        verbose_name = _("خاصية باقة")
        verbose_name_plural = _("خصائص الباقات")
        constraints = [
            models.UniqueConstraint(fields=["plan", "feature_key"], name="uq_plan_feature"),
        ]
        ordering = ["plan", "feature_key"]

    def __str__(self):
        return f"{self.plan.slug}:{self.feature_key}"


class Tenant(TimeStampedModel):
    """One client — a tutoring center company (docs/10 §N.2).

    ``slug`` is the DNS label of the center's subdomain and is therefore
    immutable after creation: changing it would break every bookmark, every
    saved login and the TLS certificate at the same time.
    """

    slug = models.CharField(
        _("المعرّف"), max_length=32, unique=True, validators=[validate_slug], db_index=True
    )
    name = models.CharField(_("اسم السنتر"), max_length=150)
    name_en = models.CharField(_("الاسم بالإنجليزية"), max_length=150, blank=True)

    status = models.CharField(
        _("الحالة"),
        max_length=20,
        choices=TenantStatus.choices,
        default=TenantStatus.TRIAL,
        db_index=True,
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.PROTECT, related_name="tenants", verbose_name=_("الباقة")
    )

    trial_ends_at = models.DateTimeField(_("نهاية التجربة"), null=True, blank=True)
    expires_at = models.DateTimeField(_("نهاية الاشتراك"), null=True, blank=True, db_index=True)
    grace_until = models.DateTimeField(_("نهاية فترة السماح"), null=True, blank=True)

    timezone = models.CharField(_("المنطقة الزمنية"), max_length=50, default="Africa/Cairo")
    language = models.CharField(_("اللغة الافتراضية"), max_length=10, default="ar")

    owner_name = models.CharField(_("اسم المسؤول"), max_length=150, blank=True)
    owner_email = models.EmailField(_("بريد المسؤول"), blank=True)
    owner_phone = models.CharField(_("هاتف المسؤول"), max_length=20, blank=True)
    notes = models.TextField(_("ملاحظات داخلية"), blank=True)

    activated_at = models.DateTimeField(_("تاريخ التفعيل"), null=True, blank=True)
    suspended_at = models.DateTimeField(_("تاريخ الإيقاف"), null=True, blank=True)
    suspended_reason = models.TextField(_("سبب الإيقاف"), blank=True)
    archived_at = models.DateTimeField(_("تاريخ الأرشفة"), null=True, blank=True)
    purge_after = models.DateTimeField(_("يُحذف نهائيًا بعد"), null=True, blank=True)

    class Meta:
        verbose_name = _("عميل")
        verbose_name_plural = _("العملاء")
        ordering = ["name"]
        indexes = [
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self):
        return self.name or self.slug

    def clean(self):
        super().clean()
        validate_slug(self.slug)

    def save(self, *args, **kwargs):
        if self.pk:
            # The slug is the DNS name. Changing it would silently break the
            # certificate and every saved bookmark, so it is refused rather
            # than merely discouraged.
            original = type(self).objects.filter(pk=self.pk).values_list("slug", flat=True).first()
            if original is not None and original != self.slug:
                raise ValidationError({"slug": _("لا يمكن تغيير معرّف العميل بعد إنشائه.")})
        return super().save(*args, **kwargs)

    @property
    def is_operational(self) -> bool:
        """Whether the center's users may log in and work.

        ``PAST_DUE`` is deliberately included: a late invoice is a conversation,
        not a reason to stop a center from taking attendance that morning.
        """
        return self.status in OPERATIONAL_STATUSES

    @property
    def primary_domain(self):
        """The tenant's main hostname.

        Honours ``prefetch_related("domains")`` when the caller set one up —
        ``self.domains.filter(...)`` would build a fresh queryset and ignore the
        prefetch, which on the console's client list meant one query per client.
        """
        cache = getattr(self, "_prefetched_objects_cache", None)
        if cache and "domains" in cache:
            return next((d for d in self.domains.all() if d.is_primary), None)
        return self.domains.filter(is_primary=True).first()

    @property
    def primary_host(self) -> str:
        domain = self.primary_domain
        return domain.host if domain else ""

    def days_until_expiry(self) -> int | None:
        deadline = self.expires_at or self.trial_ends_at
        if deadline is None:
            return None
        return (deadline - timezone.now()).days

    # ----------------------------------------------------------- lifecycle --
    def transition_to(self, status, *, actor=None, reason: str = "", automatic: bool = False):
        """The **only** writer of ``status`` (docs/10 §N.2, TASK-110).

        Funnelling every change through one method is what makes "who suspended
        this client, and why" answerable: the audit row is written here, not by
        each caller remembering to.

        Nothing is deleted at any point. Suspension is a door, not a shredder —
        resuming restores the center byte for byte.
        """
        from .constants import PlatformAction, TenantStatus
        from .platform_audit import record as record_platform

        previous = self.status
        if previous == status:
            return self

        now = timezone.now()
        self.status = status
        changed = ["status"]

        if status == TenantStatus.ACTIVE and self.activated_at is None:
            self.activated_at = now
            changed.append("activated_at")
        if status == TenantStatus.SUSPENDED:
            self.suspended_at = now
            self.suspended_reason = reason
            changed += ["suspended_at", "suspended_reason"]
        if status == TenantStatus.ARCHIVED:
            self.archived_at = now
            self.purge_after = now + timezone.timedelta(days=self.plan.retention_days)
            changed += ["archived_at", "purge_after"]
        if previous in (TenantStatus.SUSPENDED, TenantStatus.ARCHIVED) and status in (
            TenantStatus.ACTIVE,
            TenantStatus.TRIAL,
            TenantStatus.PAST_DUE,
        ):
            self.suspended_at = None
            self.suspended_reason = ""
            self.archived_at = None
            self.purge_after = None
            changed += ["suspended_at", "suspended_reason", "archived_at", "purge_after"]

        self.save(update_fields=[*dict.fromkeys(changed), "updated_at"])

        record_platform(
            (
                {
                    TenantStatus.SUSPENDED: PlatformAction.TENANT_SUSPENDED,
                    TenantStatus.ARCHIVED: PlatformAction.TENANT_ARCHIVED,
                }.get(status, PlatformAction.TENANT_RESUMED)
                if not automatic
                else PlatformAction.SUBSCRIPTION_TRANSITION
            ),
            tenant=self,
            actor=actor,
            reason=reason,
            changes={"status": [previous, status], "automatic": automatic},
        )
        return self


class Domain(models.Model):
    """A hostname that resolves to a tenant (docs/10 §N.3).

    Looked up on every single request, so it is cached by host and every write
    invalidates that cache (see ``tenancy.signals``).
    """

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="domains", verbose_name=_("العميل")
    )
    host = models.CharField(_("النطاق"), max_length=253, unique=True, db_index=True)
    is_primary = models.BooleanField(_("النطاق الأساسي"), default=False)
    is_custom = models.BooleanField(_("نطاق خاص بالعميل"), default=False)
    verified_at = models.DateTimeField(_("تاريخ التحقق"), null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("نطاق")
        verbose_name_plural = _("النطاقات")
        ordering = ["-is_primary", "host"]
        constraints = [
            # Partial unique: exactly one primary per tenant. Postgres enforces
            # this for real; SQLite honours it too for this shape.
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_primary=True),
                name="uq_one_primary_domain_per_tenant",
            ),
        ]

    def __str__(self):
        return self.host

    def save(self, *args, **kwargs):
        # A Host header arrives in whatever case the client sent it; normalise
        # once here so the lookup and the cache key never have to care.
        self.host = (self.host or "").strip().lower().rstrip(".")
        return super().save(*args, **kwargs)

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None


class TenantFeature(models.Model):
    """A per-client override of what the plan says (docs/10 §N.8).

    Rows exist only for keys an operator deliberately moved. ``INHERIT`` is
    represented by the *absence* of a row — the console deletes rather than
    stores it, so the plan default and the override can never drift apart.
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="feature_overrides")
    feature_key = models.CharField(_("الخاصية"), max_length=60, validators=[validate_feature_key])
    state = models.CharField(
        _("الحالة"), max_length=10, choices=FeatureState.choices, default=FeatureState.INHERIT
    )
    note = models.CharField(_("ملاحظة"), max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenant_feature_changes",
    )

    class Meta:
        verbose_name = _("خاصية عميل")
        verbose_name_plural = _("خصائص العملاء")
        constraints = [
            models.UniqueConstraint(fields=["tenant", "feature_key"], name="uq_tenant_feature"),
        ]
        ordering = ["tenant", "feature_key"]

    def __str__(self):
        return f"{self.tenant.slug}:{self.feature_key}={self.state}"


class TenantUsage(models.Model):
    """Denormalised counters so the console list is one query, not N × 6.

    A cache, never a source of truth. Refreshed nightly and on demand; every
    quota decision that matters re-counts when this row is stale.
    """

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="usage")
    students = models.PositiveIntegerField(default=0)
    active_students = models.PositiveIntegerField(default=0)
    users = models.PositiveIntegerField(default=0)
    groups = models.PositiveIntegerField(default=0)
    cards = models.PositiveIntegerField(default=0)
    lessons_30d = models.PositiveIntegerField(default=0)
    scans_30d = models.PositiveIntegerField(default=0)
    payments_30d_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    storage_mb = models.PositiveIntegerField(default=0)
    computed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("استخدام عميل")
        verbose_name_plural = _("استخدام العملاء")

    def __str__(self):
        return f"{self.tenant.slug}: {self.students} students"

    def is_stale(self, seconds: int = 60) -> bool:
        if self.computed_at is None:
            return True
        return (timezone.now() - self.computed_at).total_seconds() > seconds


class PlatformAuditLog(models.Model):
    """Append-only record of what platform staff did to which client.

    Separate from ``core.AuditLog`` on purpose: that one is the *center's* trail
    and a center admin can read it. This one is yours, lives on the console
    host, and no tenant user can reach it.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_audit_entries",
        verbose_name=_("المستخدم"),
    )
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_audit_entries",
        verbose_name=_("العميل"),
    )
    action = models.CharField(_("الإجراء"), max_length=40, choices=PlatformAction.choices)
    object_repr = models.CharField(_("الوصف"), max_length=200, blank=True)
    changes = models.JSONField(_("التغييرات"), default=dict, blank=True)
    reason = models.TextField(_("السبب"), blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(_("التاريخ"), auto_now_add=True)

    class Meta:
        verbose_name = _("سجل المنصة")
        verbose_name_plural = _("سجلات المنصة")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.object_repr}"
