"""Physical card inventory (docs/03 §E).

A card is *stock*, not a document generated per student. The card knows its
current holder; attendance never points at a card, so re-carding a student is a
no-op for history.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.tenancy.base import (
    AllTenantsManager,
    TenantManager,
    TenantOwnedModel,
    TenantQuerySet,
)


class CardStatus(models.TextChoices):
    AVAILABLE = "AVAILABLE", _("متاحة")
    ASSIGNED = "ASSIGNED", _("مخصصة")
    LOST = "LOST", _("مفقودة")
    DISABLED = "DISABLED", _("معطّلة")
    REPLACED = "REPLACED", _("مستبدلة")


class ReleaseReason(models.TextChoices):
    LOST = "LOST", _("فقدان")
    DAMAGED = "DAMAGED", _("تلف")
    REPLACED = "REPLACED", _("استبدال")
    GRADUATED = "GRADUATED", _("تخرج/مغادرة")
    DISABLED = "DISABLED", _("تعطيل")
    OTHER = "OTHER", _("أخرى")


class StudentCardQuerySet(TenantQuerySet):
    def available(self):
        return self.filter(status=CardStatus.AVAILABLE)

    def assigned(self):
        return self.filter(status=CardStatus.ASSIGNED)


class StudentCard(TenantOwnedModel):
    card_number = models.CharField(_("رقم البطاقة"), max_length=30, db_index=True)
    # Globally unique on purpose (docs/10 §N.5): the token is 64 random
    # characters, so global uniqueness costs nothing and guarantees a scanned
    # token can never resolve to two rows in two centers. The scan path still
    # filters by tenant — this is a safety net, not the authorisation.
    qr_token = models.CharField(_("رمز QR"), max_length=64, unique=True, db_index=True)
    status = models.CharField(
        _("الحالة"),
        max_length=20,
        choices=CardStatus.choices,
        default=CardStatus.AVAILABLE,
        db_index=True,
    )
    current_student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cards_held",
        verbose_name=_("الطالب الحالي"),
    )
    batch = models.CharField(_("الدفعة"), max_length=30, blank=True, db_index=True)
    issued_at = models.DateTimeField(_("تاريخ أول تخصيص"), null=True, blank=True)
    lost_at = models.DateTimeField(_("تاريخ الفقد"), null=True, blank=True)
    disabled_at = models.DateTimeField(_("تاريخ التعطيل"), null=True, blank=True)
    replaced_at = models.DateTimeField(_("تاريخ الاستبدال"), null=True, blank=True)
    replaced_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replaces",
        verbose_name=_("البطاقة البديلة"),
    )
    notes = models.TextField(_("ملاحظات"), blank=True)

    objects = TenantManager.from_queryset(StudentCardQuerySet)()
    all_tenants = AllTenantsManager.from_queryset(StudentCardQuerySet)()

    class Meta:
        verbose_name = _("بطاقة طالب")
        verbose_name_plural = _("بطاقات الطلاب")
        ordering = ["card_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "card_number"], name="uq_card_number_per_tenant"
            ),
            # One active card per student. Already tenant-scoped through
            # `current_student`, which is tenant-owned.
            models.UniqueConstraint(
                fields=["current_student"],
                condition=models.Q(status="ASSIGNED"),
                name="uq_one_active_card_per_student",
            ),
            # A card is ASSIGNED if and only if it points at a student.
            models.CheckConstraint(
                condition=(
                    models.Q(status="ASSIGNED", current_student__isnull=False)
                    | (~models.Q(status="ASSIGNED") & models.Q(current_student__isnull=True))
                ),
                name="ck_card_holder_matches_status",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status", "batch"]),
            # The scan path: tenant first, token second.
            models.Index(fields=["tenant", "qr_token"]),
        ]

    def __str__(self):
        return self.card_number

    @property
    def is_usable(self) -> bool:
        """Only an ASSIGNED card authenticates at the scanner (gate 2)."""
        return self.status == CardStatus.ASSIGNED

    @property
    def masked_token(self) -> str:
        from .tokens import mask_token

        return mask_token(self.qr_token)


class CardAssignment(TenantOwnedModel):
    """Append-only history: who held which card, when, and why it ended."""

    card = models.ForeignKey(
        StudentCard,
        on_delete=models.PROTECT,
        related_name="assignment_history",
        verbose_name=_("البطاقة"),
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="card_history",
        verbose_name=_("الطالب"),
    )
    assigned_at = models.DateTimeField(_("من"))
    released_at = models.DateTimeField(_("إلى"), null=True, blank=True)
    release_reason = models.CharField(
        _("سبب الإنهاء"), max_length=30, choices=ReleaseReason.choices, blank=True
    )
    assigned_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="card_assignments_made",
    )
    released_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="card_assignments_released",
    )
    notes = models.TextField(_("ملاحظات"), blank=True)

    class Meta:
        verbose_name = _("تخصيص بطاقة")
        verbose_name_plural = _("سجل تخصيص البطاقات")
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["card"],
                condition=models.Q(released_at__isnull=True),
                name="uq_open_assignment_per_card",
            ),
            models.UniqueConstraint(
                fields=["student"],
                condition=models.Q(released_at__isnull=True),
                name="uq_open_assignment_per_student",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(released_at__isnull=True)
                    | models.Q(released_at__gte=models.F("assigned_at"))
                ),
                name="ck_card_assign_dates",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "student", "-assigned_at"])]

    def __str__(self):
        return f"{self.card} → {self.student}"

    @property
    def is_open(self) -> bool:
        return self.released_at is None
