from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

# Re-exported: `from apps.core.models import TimeStampedModel` is the import
# every domain app already uses. The definition moved to core.base only to keep
# core and tenancy from importing each other — see apps/core/base.py.
from apps.tenancy.base import TenantOwnedModel

from .base import TimeStampedModel  # noqa: F401


class Setting(TenantOwnedModel):
    """A policy *override*, per center.

    Defaults live in :mod:`apps.core.policies`; only changed values are stored
    here. Metadata (label, type, group, bounds) belongs to the PolicySpec, not
    to the row, so the two can never drift.

    ``key`` was the primary key before tenancy; it is now unique *within* a
    tenant, which is what lets two centers hold different fees under the same
    policy name. The registry's cache key is namespaced to match — a shared one
    would serve one center's fees to another, and no queryset filter would catch
    it (docs/10 §N.7).
    """

    key = models.CharField(_("المفتاح"), max_length=100, db_index=True)
    value = models.JSONField(_("القيمة"))
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="setting_changes",
        verbose_name=_("عُدّل بواسطة"),
    )

    class Meta:
        verbose_name = _("إعداد")
        verbose_name_plural = _("الإعدادات")
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "key"], name="uq_setting_key_per_tenant"),
        ]

    def __str__(self):
        return f"{self.key} = {self.value!r}"


class AuditAction(models.TextChoices):
    # accounts
    USER_CREATED = "USER_CREATED", _("إنشاء مستخدم")
    USER_UPDATED = "USER_UPDATED", _("تعديل مستخدم")
    SETTING_CHANGED = "SETTING_CHANGED", _("تغيير إعداد")
    # students
    STUDENT_CREATED = "STUDENT_CREATED", _("إنشاء طالب")
    STUDENT_UPDATED = "STUDENT_UPDATED", _("تعديل طالب")
    STUDENT_STATUS_CHANGED = "STUDENT_STATUS_CHANGED", _("تغيير حالة طالب")
    STUDENT_ASSIGNED = "STUDENT_ASSIGNED", _("إضافة طالب لمجموعة")
    STUDENT_UNASSIGNED = "STUDENT_UNASSIGNED", _("إنهاء اشتراك طالب بمجموعة")
    STUDENT_TRANSFERRED = "STUDENT_TRANSFERRED", _("نقل طالب بين المجموعات")
    # cards
    CARD_IMPORTED = "CARD_IMPORTED", _("استيراد بطاقات")
    CARD_ASSIGNED = "CARD_ASSIGNED", _("ربط بطاقة")
    CARD_RELEASED = "CARD_RELEASED", _("فك ربط بطاقة")
    CARD_MARKED_LOST = "CARD_MARKED_LOST", _("الإبلاغ عن فقد بطاقة")
    CARD_DISABLED = "CARD_DISABLED", _("تعطيل بطاقة")
    CARD_REPLACED = "CARD_REPLACED", _("استبدال بطاقة")
    # lessons / attendance
    LESSON_CREATED = "LESSON_CREATED", _("إنشاء حصة")
    LESSON_UPDATED = "LESSON_UPDATED", _("تعديل حصة")
    LESSON_OPENED = "LESSON_OPENED", _("فتح حصة")
    LESSON_COMPLETED = "LESSON_COMPLETED", _("إنهاء حصة")
    LESSON_CANCELLED = "LESSON_CANCELLED", _("إلغاء حصة")
    ATTENDANCE_CREATED = "ATTENDANCE_CREATED", _("تسجيل حضور")
    ATTENDANCE_MODIFIED = "ATTENDANCE_MODIFIED", _("تعديل حضور")
    ATTENDANCE_CANCELLED = "ATTENDANCE_CANCELLED", _("إلغاء حضور")
    ATTENDANCE_APPROVED = "ATTENDANCE_APPROVED", _("اعتماد حضور استثنائي")
    # money
    CHARGE_CREATED = "CHARGE_CREATED", _("إصدار رسوم")
    CHARGE_MODIFIED = "CHARGE_MODIFIED", _("تعديل رسوم")
    CHARGE_WAIVED = "CHARGE_WAIVED", _("إعفاء من رسوم")
    CHARGE_CANCELLED = "CHARGE_CANCELLED", _("إلغاء رسوم")
    PAYMENT_CREATED = "PAYMENT_CREATED", _("تحصيل دفعة")
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED", _("استرداد دفعة")
    # reports
    REPORT_EXPORTED = "REPORT_EXPORTED", _("تصدير تقرير")


class AuditLog(models.Model):
    """Append-only record of every sensitive mutation (docs/06 §I.1).

    Not a ``TenantOwnedModel``: ``tenant`` is nullable because a platform action
    belongs to no center, and the console has to read rows across every tenant.
    The center's own audit screen filters explicitly instead (TASK-102).
    """

    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="audit_entries",
        verbose_name=_("العميل"),
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
        verbose_name=_("المستخدم"),
    )
    action = models.CharField(_("الإجراء"), max_length=60, choices=AuditAction.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, null=True, blank=True)
    object_id = models.BigIntegerField(null=True, blank=True)
    target = GenericForeignKey("content_type", "object_id")
    object_repr = models.CharField(_("الوصف"), max_length=200, blank=True)
    changes = models.JSONField(_("التغييرات"), default=dict, blank=True)
    reason = models.TextField(_("السبب"), blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(_("التاريخ"), auto_now_add=True)

    class Meta:
        verbose_name = _("سجل تدقيق")
        verbose_name_plural = _("سجلات التدقيق")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "content_type", "object_id", "-created_at"]),
            models.Index(fields=["tenant", "action", "-created_at"]),
            models.Index(fields=["tenant", "actor", "-created_at"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.object_repr}"


class Sequence(TenantOwnedModel):
    """Gapless-ish counter for human-facing codes (student codes, receipts).

    Incremented inside the caller's transaction with ``select_for_update`` so
    two concurrent creations can never take the same number; the unique index on
    the target column remains the final arbiter.

    One counter per center: student codes and receipt numbers both restart at 1
    for every new client, which is what a center expects of its own paperwork.
    """

    key = models.CharField(_("المفتاح"), max_length=50, db_index=True)
    next_value = models.PositiveIntegerField(_("القيمة التالية"), default=1)

    class Meta:
        verbose_name = _("عدّاد")
        verbose_name_plural = _("العدّادات")
        constraints = [
            models.UniqueConstraint(fields=["tenant", "key"], name="uq_sequence_key_per_tenant"),
        ]

    def __str__(self):
        return f"{self.key}={self.next_value}"
