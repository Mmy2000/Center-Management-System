"""Academic structure: Stage → Grade → Subject offering → Group → Schedule.

Nothing here is hard-coded: every level is data (docs/01 §B.3). The pivotal
entity is :class:`GradeSubject` — the "this grade studies this subject" offering
that groups, assignments and billing all hang off, so a Group can never claim a
(grade, subject) pair the center does not actually offer.
"""

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class Weekday(models.IntegerChoices):
    """Egyptian week: the working week starts on Saturday."""

    SATURDAY = 0, _("السبت")
    SUNDAY = 1, _("الأحد")
    MONDAY = 2, _("الاثنين")
    TUESDAY = 3, _("الثلاثاء")
    WEDNESDAY = 4, _("الأربعاء")
    THURSDAY = 5, _("الخميس")
    FRIDAY = 6, _("الجمعة")

    @classmethod
    def from_python_weekday(cls, weekday: int) -> int:
        """Convert ``date.weekday()`` (Mon=0 … Sun=6) to this scale."""
        return (weekday + 2) % 7


class GroupStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("نشطة")
    PAUSED = "PAUSED", _("متوقفة مؤقتًا")
    CLOSED = "CLOSED", _("مغلقة")


class EducationalStage(TimeStampedModel):
    name = models.CharField(_("الاسم"), max_length=100, unique=True)
    name_ar = models.CharField(_("الاسم بالعربية"), max_length=100, blank=True)
    code = models.CharField(_("الكود"), max_length=20, unique=True)
    order = models.PositiveSmallIntegerField(_("الترتيب"), default=0)
    is_active = models.BooleanField(_("مفعّلة"), default=True)

    class Meta:
        verbose_name = _("مرحلة دراسية")
        verbose_name_plural = _("المراحل الدراسية")
        ordering = ["order", "id"]

    def __str__(self):
        return self.name_ar or self.name


class Grade(TimeStampedModel):
    stage = models.ForeignKey(
        EducationalStage,
        on_delete=models.PROTECT,
        related_name="grades",
        verbose_name=_("المرحلة"),
    )
    name = models.CharField(_("الاسم"), max_length=100)
    name_ar = models.CharField(_("الاسم بالعربية"), max_length=100, blank=True)
    code = models.CharField(_("الكود"), max_length=20, unique=True)
    order = models.PositiveSmallIntegerField(_("الترتيب"), default=0)
    is_active = models.BooleanField(_("مفعّل"), default=True)

    class Meta:
        verbose_name = _("صف دراسي")
        verbose_name_plural = _("الصفوف الدراسية")
        ordering = ["stage__order", "order", "id"]
        constraints = [
            models.UniqueConstraint(fields=["stage", "name"], name="uq_grade_name_per_stage"),
        ]
        indexes = [models.Index(fields=["stage", "order"])]

    def __str__(self):
        return self.name_ar or self.name


class Subject(TimeStampedModel):
    name = models.CharField(_("الاسم"), max_length=100, unique=True)
    name_ar = models.CharField(_("الاسم بالعربية"), max_length=100, blank=True)
    code = models.CharField(_("الكود"), max_length=20, unique=True)
    color = models.CharField(_("اللون"), max_length=7, default="#2b6cb0")
    is_active = models.BooleanField(_("مفعّلة"), default=True)

    class Meta:
        verbose_name = _("مادة")
        verbose_name_plural = _("المواد")
        ordering = ["name"]

    def __str__(self):
        return self.name_ar or self.name


class GradeSubject(TimeStampedModel):
    """A subject *offering*: this grade studies this subject, at this fee."""

    grade = models.ForeignKey(
        Grade, on_delete=models.PROTECT, related_name="offerings", verbose_name=_("الصف")
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name="offerings", verbose_name=_("المادة")
    )
    default_monthly_fee = models.DecimalField(
        _("الرسوم الشهرية الافتراضية"),
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    is_active = models.BooleanField(_("مفعّلة"), default=True)

    class Meta:
        verbose_name = _("مادة لصف")
        verbose_name_plural = _("مواد الصفوف")
        ordering = ["grade__order", "subject__name"]
        constraints = [
            models.UniqueConstraint(fields=["grade", "subject"], name="uq_offering_grade_subject"),
            models.CheckConstraint(
                condition=models.Q(default_monthly_fee__gte=0),
                name="ck_offering_fee_non_negative",
            ),
        ]
        indexes = [models.Index(fields=["subject", "grade"])]

    def __str__(self):
        return f"{self.subject} — {self.grade}"

    @property
    def stage(self):
        return self.grade.stage


class Instructor(TimeStampedModel):
    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instructor",
        verbose_name=_("حساب المستخدم"),
    )
    full_name = models.CharField(_("الاسم"), max_length=150)
    phone = models.CharField(_("الهاتف"), max_length=20, blank=True)
    subjects = models.ManyToManyField(
        Subject, blank=True, related_name="instructors", verbose_name=_("المواد")
    )
    is_active = models.BooleanField(_("مفعّل"), default=True)
    notes = models.TextField(_("ملاحظات"), blank=True)

    class Meta:
        verbose_name = _("مدرّس")
        verbose_name_plural = _("المدرّسون")
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name


class Group(TimeStampedModel):
    grade_subject = models.ForeignKey(
        GradeSubject,
        on_delete=models.PROTECT,
        related_name="groups",
        verbose_name=_("المادة/الصف"),
    )
    name = models.CharField(_("الاسم"), max_length=100)
    name_ar = models.CharField(_("الاسم بالعربية"), max_length=100, blank=True)
    code = models.CharField(_("الكود"), max_length=30, unique=True)
    instructor = models.ForeignKey(
        Instructor,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="groups",
        verbose_name=_("المدرّس"),
    )
    capacity = models.PositiveSmallIntegerField(_("السعة"), default=0, help_text=_("صفر = بلا حد"))
    monthly_fee = models.DecimalField(
        _("الرسوم الشهرية"),
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    academic_year = models.CharField(_("العام الدراسي"), max_length=9, blank=True)
    status = models.CharField(
        _("الحالة"), max_length=20, choices=GroupStatus.choices, default=GroupStatus.ACTIVE
    )
    default_late_after_minutes = models.PositiveSmallIntegerField(
        _("حد التأخير (دقائق)"), null=True, blank=True
    )
    notes = models.TextField(_("ملاحظات"), blank=True)

    class Meta:
        verbose_name = _("مجموعة")
        verbose_name_plural = _("المجموعات")
        ordering = ["grade_subject__grade__order", "grade_subject__subject__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["grade_subject", "name"], name="uq_group_name_per_offering"
            ),
            models.CheckConstraint(
                condition=models.Q(monthly_fee__gte=0), name="ck_group_fee_non_negative"
            ),
        ]
        indexes = [
            models.Index(fields=["grade_subject", "status"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.subject} — {self.name_ar or self.name}"

    # Derived, never stored (docs/01 §A.3 A5).
    @property
    def subject(self):
        return self.grade_subject.subject

    @property
    def grade(self):
        return self.grade_subject.grade

    @property
    def stage(self):
        return self.grade_subject.grade.stage

    @property
    def is_open_for_enrollment(self) -> bool:
        return self.status == GroupStatus.ACTIVE


class GroupSchedule(TimeStampedModel):
    """A recurring weekly slot; lessons are generated from these."""

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="schedules", verbose_name=_("المجموعة")
    )
    weekday = models.PositiveSmallIntegerField(_("اليوم"), choices=Weekday.choices)
    start_time = models.TimeField(_("من"))
    end_time = models.TimeField(_("إلى"))
    room = models.CharField(_("القاعة"), max_length=30, blank=True)
    is_active = models.BooleanField(_("مفعّل"), default=True)

    class Meta:
        verbose_name = _("موعد أسبوعي")
        verbose_name_plural = _("المواعيد الأسبوعية")
        ordering = ["weekday", "start_time"]
        constraints = [
            models.UniqueConstraint(
                fields=["group", "weekday", "start_time"], name="uq_schedule_slot"
            ),
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="ck_schedule_time_order",
            ),
            models.CheckConstraint(
                condition=models.Q(weekday__gte=0) & models.Q(weekday__lte=6),
                name="ck_schedule_weekday_range",
            ),
        ]

    def __str__(self):
        return f"{self.get_weekday_display()} {self.start_time:%H:%M}–{self.end_time:%H:%M}"
