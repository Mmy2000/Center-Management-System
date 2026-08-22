"""The durable student record (docs/02 §4).

Deliberately absent from this table: ``group_id``, ``card_id`` and any
``is_paid`` flag. Groups, cards, attendance and money are separate entities that
point *at* the student, which is what lets a student change group, change card
and pay in instalments without any history being rewritten.
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel
from apps.core.text import normalize_arabic


def student_photo_path(instance, filename: str) -> str:
    """Non-sequential, non-guessable media path (docs/06 §I.2)."""
    extension = (filename.rsplit(".", 1)[-1] or "jpg").lower()[:5]
    return f"students/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{extension}"


class StudentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("نشط")
    INACTIVE = "INACTIVE", _("غير نشط")
    SUSPENDED = "SUSPENDED", _("موقوف")
    TRANSFERRED = "TRANSFERRED", _("منقول")
    GRADUATED = "GRADUATED", _("متخرج")


class Gender(models.TextChoices):
    MALE = "MALE", _("ذكر")
    FEMALE = "FEMALE", _("أنثى")


class GuardianRelation(models.TextChoices):
    FATHER = "FATHER", _("الأب")
    MOTHER = "MOTHER", _("الأم")
    OTHER = "OTHER", _("آخر")


class StudentQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=StudentStatus.ACTIVE)

    def with_related(self):
        return self.select_related("grade__stage")


class Student(TimeStampedModel):
    student_code = models.CharField(_("كود الطالب"), max_length=20, unique=True, db_index=True)
    full_name = models.CharField(_("الاسم الكامل"), max_length=150, db_index=True)
    search_name = models.CharField(max_length=150, blank=True, db_index=True, editable=False)
    photo = models.ImageField(_("الصورة"), upload_to=student_photo_path, blank=True, null=True)
    date_of_birth = models.DateField(_("تاريخ الميلاد"), null=True, blank=True)
    gender = models.CharField(_("النوع"), max_length=10, choices=Gender.choices, blank=True)
    phone = models.CharField(_("هاتف الطالب"), max_length=20, blank=True, db_index=True)

    guardian_name = models.CharField(_("ولي الأمر"), max_length=150, blank=True)
    guardian_phone = models.CharField(_("هاتف ولي الأمر"), max_length=20, blank=True, db_index=True)
    guardian_relation = models.CharField(
        _("صلة القرابة"), max_length=20, choices=GuardianRelation.choices, blank=True
    )
    address = models.TextField(_("العنوان"), blank=True)

    grade = models.ForeignKey(
        "academics.Grade",
        on_delete=models.PROTECT,
        related_name="students",
        verbose_name=_("الصف"),
    )
    school = models.CharField(_("المدرسة"), max_length=150, blank=True)
    status = models.CharField(
        _("الحالة"),
        max_length=20,
        choices=StudentStatus.choices,
        default=StudentStatus.ACTIVE,
        db_index=True,
    )
    enrolled_on = models.DateField(_("تاريخ الالتحاق"), default=timezone.localdate)
    notes = models.TextField(_("ملاحظات"), blank=True)

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students_created",
    )
    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students_updated",
    )

    objects = StudentQuerySet.as_manager()

    class Meta:
        verbose_name = _("طالب")
        verbose_name_plural = _("الطلاب")
        ordering = ["full_name"]
        indexes = [
            models.Index(fields=["status", "grade"]),
            models.Index(fields=["full_name"]),
            models.Index(fields=["search_name"]),
            models.Index(fields=["guardian_phone"]),
        ]

    def save(self, *args, **kwargs):
        """Keep the folded search column in step with the display name."""
        self.search_name = normalize_arabic(self.full_name)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "full_name" in update_fields:
            kwargs["update_fields"] = list(set(update_fields) | {"search_name"})
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.full_name} ({self.student_code})"

    @property
    def stage(self):
        """Derived — never stored twice (docs/01 §A.3 A5)."""
        return self.grade.stage

    @property
    def is_active(self) -> bool:
        return self.status == StudentStatus.ACTIVE

    @property
    def can_attend(self) -> bool:
        """Suspended/inactive students are refused at the scanner (gate 3)."""
        return self.status == StudentStatus.ACTIVE


class AssignmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", _("نشط")
    ENDED = "ENDED", _("منتهي")
    TRANSFERRED = "TRANSFERRED", _("منقول")
    CANCELLED = "CANCELLED", _("ملغي")


class EndReason(models.TextChoices):
    GROUP_CHANGE = "GROUP_CHANGE", _("تغيير مجموعة")
    LEFT_CENTER = "LEFT_CENTER", _("ترك السنتر")
    SCHEDULE = "SCHEDULE", _("تعارض مواعيد")
    OTHER = "OTHER", _("أخرى")


class StudentGroupAssignment(TimeStampedModel):
    """The student's *usual* group for one subject offering (docs/01 §B.3).

    History-preserving: changing groups ends this row and opens a new one; the
    old row keeps pointing at the old group forever, which is what makes the
    alternative-group report historically truthful.

    ``grade_subject`` is denormalized from ``group`` (validated in clean()) so
    that "the active assignment for this subject" is a single indexed row — the
    integrity anchor *and* the hot-path lookup during a QR scan.
    """

    student = models.ForeignKey(
        Student, on_delete=models.PROTECT, related_name="assignments", verbose_name=_("الطالب")
    )
    group = models.ForeignKey(
        "academics.Group",
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name=_("المجموعة"),
    )
    grade_subject = models.ForeignKey(
        "academics.GradeSubject",
        on_delete=models.PROTECT,
        related_name="assignments",
        verbose_name=_("المادة/الصف"),
    )
    is_default = models.BooleanField(_("المجموعة الأساسية"), default=True)
    start_date = models.DateField(_("من"), default=timezone.localdate)
    end_date = models.DateField(_("إلى"), null=True, blank=True)
    status = models.CharField(
        _("الحالة"),
        max_length=20,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.ACTIVE,
    )
    end_reason = models.CharField(
        _("سبب الإنهاء"), max_length=30, choices=EndReason.choices, blank=True
    )
    notes = models.TextField(_("ملاحظات"), blank=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments_created",
    )

    class Meta:
        verbose_name = _("اشتراك طالب بمجموعة")
        verbose_name_plural = _("اشتراكات الطلاب بالمجموعات")
        ordering = ["-start_date", "-id"]
        constraints = [
            # A4: at most one ACTIVE assignment per (student, offering).
            models.UniqueConstraint(
                fields=["student", "grade_subject"],
                condition=models.Q(status="ACTIVE"),
                name="uq_active_assignment_per_offering",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(end_date__isnull=True)
                    | models.Q(end_date__gte=models.F("start_date"))
                ),
                name="ck_assignment_date_order",
            ),
        ]
        indexes = [
            models.Index(fields=["student", "status"]),
            models.Index(fields=["group", "status"]),
            models.Index(fields=["grade_subject", "status"]),
        ]

    def __str__(self):
        return f"{self.student} → {self.group}"

    def clean(self):
        super().clean()
        if self.group_id and self.grade_subject_id:
            if self.group.grade_subject_id != self.grade_subject_id:
                raise ValidationError(
                    {"grade_subject": _("المادة/الصف يجب أن تطابق مجموعة الطالب.")}
                )

    def save(self, *args, **kwargs):
        # Keep the denormalized offering in step with the group, always.
        if self.group_id:
            self.grade_subject_id = self.group.grade_subject_id
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        return self.status == AssignmentStatus.ACTIVE
