"""Attendance — the table that encodes Principle 2 (docs/02 §7).

``assigned_group`` and ``attended_group`` are two separate columns, resolved at
scan time and never back-filled. That is what keeps the alternative-group report
historically truthful after a student permanently moves to another group.

Three orthogonal axes, never collapsed:
    state            where the student is now       (lifecycle)
    status           how it counts in reports       (outcome)
    attendance_type  why/how it happened            (provenance)
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.tenancy.base import (
    AllTenantsManager,
    TenantManager,
    TenantOwnedModel,
    TenantQuerySet,
)


class AttendanceState(models.TextChoices):
    """``NOT_ATTENDED`` is the implicit initial state: no row exists."""

    ABSENT = "ABSENT", _("غائب")
    CHECKED_IN = "CHECKED_IN", _("بالداخل")
    CHECKED_OUT = "CHECKED_OUT", _("انصرف")
    CANCELLED = "CANCELLED", _("ملغي")


class AttendanceStatus(models.TextChoices):
    PRESENT = "PRESENT", _("حاضر")
    LATE = "LATE", _("متأخر")
    ABSENT = "ABSENT", _("غائب")
    PARTIAL = "PARTIAL", _("حضور جزئي")
    CANCELLED = "CANCELLED", _("ملغي")


class AttendanceType(models.TextChoices):
    NORMAL = "NORMAL", _("عادي")
    ALTERNATIVE_GROUP = "ALTERNATIVE_GROUP", _("مجموعة بديلة")
    MAKEUP = "MAKEUP", _("تعويضية")
    MANUAL = "MANUAL", _("يدوي")
    EXCEPTIONAL = "EXCEPTIONAL", _("استثنائي")


class ScanSource(models.TextChoices):
    HID = "HID", _("قارئ باركود")
    CAMERA = "CAMERA", _("كاميرا")
    MANUAL = "MANUAL", _("يدوي")
    SYSTEM = "SYSTEM", _("النظام")


class AttendanceQuerySet(TenantQuerySet):
    def with_related(self):
        return self.select_related(
            "student",
            "lesson__group__grade_subject__subject",
            "assigned_group",
            "attended_group",
        )

    def present(self):
        return self.filter(state__in=[AttendanceState.CHECKED_IN, AttendanceState.CHECKED_OUT])

    def alternative(self):
        return self.exclude(attendance_type=AttendanceType.NORMAL).exclude(
            state=AttendanceState.ABSENT
        )


class Attendance(TenantOwnedModel):
    lesson = models.ForeignKey(
        "lessons.Lesson",
        on_delete=models.PROTECT,
        related_name="attendances",
        verbose_name=_("الحصة"),
    )
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.PROTECT,
        related_name="attendances",
        verbose_name=_("الطالب"),
    )
    assigned_group = models.ForeignKey(
        "academics.Group",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="attendance_as_assigned",
        verbose_name=_("مجموعته المعتادة"),
    )
    attended_group = models.ForeignKey(
        "academics.Group",
        on_delete=models.PROTECT,
        related_name="attendance_as_attended",
        verbose_name=_("المجموعة الحالية"),
    )

    state = models.CharField(
        _("الوضع"), max_length=20, choices=AttendanceState.choices, db_index=True
    )
    status = models.CharField(
        _("الحالة"), max_length=20, choices=AttendanceStatus.choices, db_index=True
    )
    attendance_type = models.CharField(
        _("النوع"),
        max_length=20,
        choices=AttendanceType.choices,
        default=AttendanceType.NORMAL,
        db_index=True,
    )

    check_in_at = models.DateTimeField(_("الحضور"), null=True, blank=True)
    check_out_at = models.DateTimeField(_("الانصراف"), null=True, blank=True)
    late_minutes = models.PositiveSmallIntegerField(_("دقائق التأخير"), default=0)
    duration_minutes = models.PositiveSmallIntegerField(_("المدة"), null=True, blank=True)

    makeup_for_lesson = models.ForeignKey(
        "lessons.Lesson",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="makeups",
        verbose_name=_("تعويض عن حصة"),
    )
    card_used = models.ForeignKey(
        "cards.StudentCard",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendances",
        verbose_name=_("البطاقة المستخدمة"),
    )

    is_manual = models.BooleanField(_("تدخل يدوي"), default=False)
    requires_approval = models.BooleanField(_("يحتاج اعتمادًا"), default=False)
    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_approvals",
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    checked_in_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_check_ins",
    )
    checked_out_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_check_outs",
    )
    scan_source = models.CharField(
        _("مصدر التسجيل"), max_length=20, choices=ScanSource.choices, default=ScanSource.HID
    )
    notes = models.TextField(_("ملاحظات"), blank=True)

    objects = TenantManager.from_queryset(AttendanceQuerySet)()
    all_tenants = AllTenantsManager.from_queryset(AttendanceQuerySet)()

    class Meta:
        verbose_name = _("حضور")
        verbose_name_plural = _("سجلات الحضور")
        ordering = ["-check_in_at", "-id"]
        constraints = [
            # The idempotency anchor: two scanners racing produce one row.
            # Already tenant-scoped through `lesson`, which is tenant-owned.
            models.UniqueConstraint(
                fields=["lesson", "student"], name="uq_attendance_lesson_student"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(check_out_at__isnull=True)
                    | models.Q(check_out_at__gte=models.F("check_in_at"))
                ),
                name="ck_attendance_time_order",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(state__in=["CHECKED_IN", "CHECKED_OUT"], check_in_at__isnull=False)
                    | models.Q(state__in=["ABSENT", "CANCELLED"])
                ),
                name="ck_attendance_state_requires_checkin",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(attendance_type="MAKEUP") | models.Q(makeup_for_lesson__isnull=False)
                ),
                name="ck_makeup_requires_source_lesson",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "lesson", "state"]),
            models.Index(fields=["tenant", "student", "-check_in_at"]),
            models.Index(fields=["tenant", "attended_group", "lesson"]),
            models.Index(fields=["tenant", "-check_in_at"], name="ix_att_recent"),
            models.Index(
                fields=["tenant", "lesson", "attendance_type"],
                condition=~models.Q(attendance_type="NORMAL"),
                name="ix_att_alternative",
            ),
        ]
        permissions = [
            ("scan_any_group", _("المسح لأي مجموعة")),
            ("approve_exceptional", _("اعتماد الحضور الاستثنائي")),
            ("correct_attendance", _("تصحيح الحضور يدويًا")),
        ]

    def __str__(self):
        return f"{self.student} @ {self.lesson}"

    @property
    def is_alternative(self) -> bool:
        return (
            self.assigned_group_id is not None and self.assigned_group_id != self.attended_group_id
        )

    @property
    def is_inside(self) -> bool:
        return self.state == AttendanceState.CHECKED_IN


class AttendanceEventType(models.TextChoices):
    CHECK_IN = "CHECK_IN", _("تسجيل حضور")
    CHECK_OUT = "CHECK_OUT", _("تسجيل انصراف")
    DUPLICATE = "DUPLICATE", _("مسح مكرر")
    DENIED = "DENIED", _("مرفوض")
    MANUAL_EDIT = "MANUAL_EDIT", _("تعديل يدوي")
    CANCEL = "CANCEL", _("إلغاء")
    APPROVAL = "APPROVAL", _("اعتماد")


class AttendanceEvent(TenantOwnedModel):
    """Append-only forensic log of every scan attempt, including refusals.

    Never stores the raw QR token — only the card it resolved to.
    """

    attendance = models.ForeignKey(
        Attendance, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    lesson = models.ForeignKey("lessons.Lesson", on_delete=models.PROTECT, related_name="events")
    student = models.ForeignKey(
        "students.Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_events",
    )
    card = models.ForeignKey(
        "cards.StudentCard", on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )

    event_type = models.CharField(max_length=30, choices=AttendanceEventType.choices, db_index=True)
    result_code = models.CharField(max_length=40, db_index=True)
    message = models.CharField(max_length=200, blank=True)
    device_id = models.CharField(max_length=60, blank=True, db_index=True)
    operator = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scan_events",
    )
    # Client-generated, so two centers will eventually pick the same value:
    # unique per tenant, never globally (docs/10 §N.5).
    # null, not , precisely so the partial unique constraint below can
    # ignore the events that carry no key: NULLs do not collide, empty
    # strings do. (Ruff's DJ001 waives this for unique=True fields; the
    # reasoning is identical now that uniqueness lives in a constraint.)
    idempotency_key = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = _("حدث مسح")
        verbose_name_plural = _("أحداث المسح")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uq_event_idempotency_per_tenant",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "lesson", "-created_at"]),
            models.Index(fields=["tenant", "result_code", "-created_at"]),
            models.Index(fields=["tenant", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.created_at:%H:%M:%S} {self.result_code}"
