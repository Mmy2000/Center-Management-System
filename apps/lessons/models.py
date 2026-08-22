"""Concrete dated sessions of a group (docs/02 §6).

Times are stored as timezone-aware datetimes, never as date+time pairs: Egypt
observes DST, so a naive comparison breaks twice a year. ``lesson_date`` is the
*local* date, derived on save, and exists only so reports and uniqueness can use
one indexed column.
"""

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class LessonStatus(models.TextChoices):
    SCHEDULED = "SCHEDULED", _("مجدولة")
    OPEN = "OPEN", _("جارية")
    COMPLETED = "COMPLETED", _("منتهية")
    CANCELLED = "CANCELLED", _("ملغاة")


class WindowState(models.TextChoices):
    BEFORE_OPEN = "BEFORE_OPEN", _("قبل بدء التسجيل")
    OPEN = "OPEN", _("التسجيل مفتوح")
    AFTER_CLOSE = "AFTER_CLOSE", _("التسجيل مغلق")


class LessonQuerySet(models.QuerySet):
    def with_related(self):
        return self.select_related(
            "group__grade_subject__subject",
            "group__grade_subject__grade__stage",
            "group__instructor",
            "instructor",
        )

    def active(self):
        return self.filter(status=LessonStatus.OPEN)

    def for_date(self, day):
        return self.filter(lesson_date=day)


class Lesson(TimeStampedModel):
    group = models.ForeignKey(
        "academics.Group",
        on_delete=models.PROTECT,
        related_name="lessons",
        verbose_name=_("المجموعة"),
    )
    instructor = models.ForeignKey(
        "academics.Instructor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lessons",
        verbose_name=_("المدرّس"),
    )
    sequence_no = models.PositiveSmallIntegerField(_("رقم الحصة"), null=True, blank=True)

    lesson_date = models.DateField(_("التاريخ"), db_index=True)
    scheduled_start = models.DateTimeField(_("البداية"), db_index=True)
    scheduled_end = models.DateTimeField(_("النهاية"))
    check_in_opens_at = models.DateTimeField(_("فتح التسجيل"))
    check_in_closes_at = models.DateTimeField(_("إغلاق التسجيل"))
    late_after = models.DateTimeField(_("اعتبار التأخير بعد"))

    status = models.CharField(
        _("الحالة"),
        max_length=20,
        choices=LessonStatus.choices,
        default=LessonStatus.SCHEDULED,
        db_index=True,
    )
    actual_start_at = models.DateTimeField(_("الفتح الفعلي"), null=True, blank=True)
    actual_end_at = models.DateTimeField(_("الإنهاء الفعلي"), null=True, blank=True)
    expected_students = models.PositiveSmallIntegerField(_("العدد المتوقع"), default=0)

    cancel_reason = models.TextField(_("سبب الإلغاء"), blank=True)
    notes = models.TextField(_("ملاحظات"), blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lessons_created",
    )

    objects = LessonQuerySet.as_manager()

    class Meta:
        verbose_name = _("حصة")
        verbose_name_plural = _("الحصص")
        ordering = ["-scheduled_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "scheduled_start"], name="uq_lesson_group_start"
            ),
            models.CheckConstraint(
                condition=models.Q(scheduled_end__gt=models.F("scheduled_start")),
                name="ck_lesson_time_order",
            ),
            models.CheckConstraint(
                condition=models.Q(check_in_closes_at__gt=models.F("check_in_opens_at")),
                name="ck_lesson_window_order",
            ),
        ]
        indexes = [
            models.Index(fields=["group", "lesson_date"]),
            models.Index(fields=["status", "scheduled_start"]),
            models.Index(fields=["lesson_date", "status"]),
        ]
        permissions = [
            ("open_lesson", _("فتح الحصة")),
            ("complete_lesson", _("إنهاء الحصة")),
            ("cancel_lesson", _("إلغاء الحصة")),
            ("generate_lessons", _("توليد الحصص")),
        ]

    def __str__(self):
        return f"{self.group} — {self.lesson_date:%d/%m/%Y}"

    def save(self, *args, **kwargs):
        # lesson_date always follows the *local* start instant.
        if self.scheduled_start:
            self.lesson_date = timezone.localtime(self.scheduled_start).date()
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "scheduled_start" in update_fields:
                kwargs["update_fields"] = list(set(update_fields) | {"lesson_date"})
        super().save(*args, **kwargs)

    # ----------------------------------------------------------------- state
    @property
    def is_open(self) -> bool:
        return self.status == LessonStatus.OPEN

    @property
    def is_scannable(self) -> bool:
        return self.status == LessonStatus.OPEN

    def window_state(self, now=None) -> str:
        now = now or timezone.now()
        if now < self.check_in_opens_at:
            return WindowState.BEFORE_OPEN
        if now > self.check_in_closes_at:
            return WindowState.AFTER_CLOSE
        return WindowState.OPEN

    def late_minutes_for(self, moment) -> int:
        """Minutes past the scheduled start, floored at zero."""
        if moment <= self.scheduled_start:
            return 0
        return int((moment - self.scheduled_start).total_seconds() // 60)

    def is_late(self, moment) -> bool:
        return moment > self.late_after
