"""Monthly charges and the payment ledger (docs/02 §8, docs/04).

For every (student, subject-offering, month) the center issues **one
obligation**; against it the student posts **many transactions**; the balance
and the status are always **derived**, never typed.

Nothing here has a foreign key to attendance, in either direction.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


class ChargeStatus(models.TextChoices):
    UNPAID = "UNPAID", _("غير مسدد")
    PARTIALLY_PAID = "PARTIALLY_PAID", _("مسدد جزئيًا")
    PAID = "PAID", _("مسدد")
    OVERPAID = "OVERPAID", _("سداد زائد")
    WAIVED = "WAIVED", _("معفى")
    CANCELLED = "CANCELLED", _("ملغي")

    @classmethod
    def sticky(cls):
        """Administrative states that derivation must never overwrite."""
        return (cls.WAIVED, cls.CANCELLED)


class PaymentKind(models.TextChoices):
    PAYMENT = "PAYMENT", _("تحصيل")
    REFUND = "REFUND", _("استرداد")


class PaymentMethod(models.TextChoices):
    CASH = "CASH", _("نقدًا")
    BANK_TRANSFER = "BANK_TRANSFER", _("تحويل بنكي")
    CARD = "CARD", _("بطاقة")
    WALLET = "WALLET", _("محفظة إلكترونية")
    OTHER = "OTHER", _("أخرى")


class GeneratedBy(models.TextChoices):
    AUTO = "AUTO", _("تلقائي")
    MANUAL = "MANUAL", _("يدوي")


class MonthlyChargeQuerySet(models.QuerySet):
    def outstanding(self):
        return self.filter(balance__gt=0).exclude(
            status__in=[ChargeStatus.CANCELLED, ChargeStatus.WAIVED]
        )

    def billable(self):
        """Rows that count towards expected revenue."""
        return self.exclude(status__in=[ChargeStatus.CANCELLED, ChargeStatus.WAIVED])

    def with_related(self):
        return self.select_related("student", "grade_subject__subject", "grade_subject__grade", "group")


class MonthlyCharge(TimeStampedModel):
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="charges",
        verbose_name=_("الطالب"),
    )
    grade_subject = models.ForeignKey(
        "academics.GradeSubject",
        on_delete=models.PROTECT,
        related_name="charges",
        verbose_name=_("المادة/الصف"),
        help_text=_("مرساة الفوترة — لا تتأثر بتغيير المجموعة"),
    )
    group = models.ForeignKey(
        "academics.Group",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="charges",
        verbose_name=_("المجموعة وقت الإصدار"),
    )
    billing_month = models.DateField(_("شهر الفوترة"), db_index=True)

    amount_due = models.DecimalField(_("المستحق"), max_digits=10, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(_("الخصم"), max_digits=10, decimal_places=2, default=ZERO)
    total_paid = models.DecimalField(_("المدفوع"), max_digits=10, decimal_places=2, default=ZERO)
    balance = models.GeneratedField(
        expression=models.F("amount_due") - models.F("discount_amount") - models.F("total_paid"),
        output_field=models.DecimalField(max_digits=10, decimal_places=2),
        db_persist=True,
        verbose_name=_("المتبقي"),
    )

    status = models.CharField(
        _("الحالة"),
        max_length=20,
        choices=ChargeStatus.choices,
        default=ChargeStatus.UNPAID,
        db_index=True,
    )
    due_date = models.DateField(_("تاريخ الاستحقاق"), null=True, blank=True)
    waived_reason = models.TextField(_("سبب الإعفاء"), blank=True)
    generated_by = models.CharField(
        max_length=20, choices=GeneratedBy.choices, default=GeneratedBy.AUTO
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="charges_created",
    )
    updated_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="charges_updated",
    )

    objects = MonthlyChargeQuerySet.as_manager()

    class Meta:
        verbose_name = _("رسوم شهرية")
        verbose_name_plural = _("الرسوم الشهرية")
        ordering = ["-billing_month", "student__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "grade_subject", "billing_month"],
                name="uq_charge_per_month",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(amount_due__gte=0)
                    & models.Q(discount_amount__gte=0)
                    & models.Q(total_paid__gte=0)
                ),
                name="ck_charge_amounts_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(discount_amount__lte=models.F("amount_due")),
                name="ck_discount_not_exceeding_due",
            ),
        ]
        indexes = [
            models.Index(fields=["billing_month", "status"]),
            models.Index(fields=["student", "-billing_month"]),
            models.Index(
                fields=["billing_month"],
                condition=models.Q(balance__gt=0),
                name="ix_charge_outstanding",
            ),
            models.Index(fields=["group", "billing_month"]),
        ]
        permissions = [
            ("waive_charge", _("الإعفاء من الرسوم")),
            ("generate_charges", _("إصدار رسوم الشهر")),
        ]

    def __str__(self):
        return f"{self.student} — {self.grade_subject} — {self.billing_month:%Y-%m}"

    def save(self, *args, **kwargs):
        if self.billing_month:
            self.billing_month = self.billing_month.replace(day=1)
        super().save(*args, **kwargs)

    @property
    def net_due(self) -> Decimal:
        return self.amount_due - self.discount_amount

    @property
    def is_settled(self) -> bool:
        return self.status in (ChargeStatus.PAID, ChargeStatus.WAIVED, ChargeStatus.CANCELLED)

    @property
    def subject(self):
        return self.grade_subject.subject


class PaymentQuerySet(models.QuerySet):
    def payments(self):
        return self.filter(kind=PaymentKind.PAYMENT)

    def refunds(self):
        return self.filter(kind=PaymentKind.REFUND)


class Payment(TimeStampedModel):
    """An immutable ledger line. Never updated, never deleted — corrected by a
    REFUND that points back at the original."""

    monthly_charge = models.ForeignKey(
        MonthlyCharge,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name=_("الرسوم"),
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="payments",
        db_index=True,
        verbose_name=_("الطالب"),
    )
    kind = models.CharField(
        _("النوع"), max_length=10, choices=PaymentKind.choices, default=PaymentKind.PAYMENT
    )
    amount = models.DecimalField(_("المبلغ"), max_digits=10, decimal_places=2)
    paid_at = models.DateTimeField(_("التاريخ"), db_index=True)
    method = models.CharField(
        _("طريقة الدفع"), max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    receipt_number = models.CharField(_("رقم الإيصال"), max_length=30, unique=True)
    reference = models.CharField(_("مرجع"), max_length=60, blank=True)
    collected_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="payments_collected",
        verbose_name=_("حصّلها"),
    )
    reverses = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reversed_by",
        verbose_name=_("يُلغي الدفعة"),
    )
    notes = models.TextField(_("ملاحظات"), blank=True)

    objects = PaymentQuerySet.as_manager()

    class Meta:
        verbose_name = _("حركة مالية")
        verbose_name_plural = _("الحركات المالية")
        ordering = ["-paid_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="ck_payment_amount_positive"),
        ]
        indexes = [
            models.Index(fields=["student", "-paid_at"]),
            models.Index(fields=["monthly_charge", "paid_at"]),
            models.Index(fields=["paid_at", "method"]),
            models.Index(fields=["collected_by", "paid_at"]),
        ]
        permissions = [("refund_payment", _("استرداد دفعة"))]

    MUTABLE_AFTER_CREATION = {"notes", "updated_at"}

    def __str__(self):
        sign = "-" if self.kind == PaymentKind.REFUND else "+"
        return f"{self.receipt_number} {sign}{self.amount}"

    def save(self, *args, **kwargs):
        """Immutability guard: only ``notes`` may change after creation."""
        if self.pk is not None:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_AFTER_CREATION:
                raise ValidationError(
                    _("لا يمكن تعديل حركة مالية — استخدم الاسترداد بدلًا من ذلك.")
                )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(_("لا يمكن حذف حركة مالية."))

    @property
    def signed_amount(self) -> Decimal:
        return -self.amount if self.kind == PaymentKind.REFUND else self.amount
