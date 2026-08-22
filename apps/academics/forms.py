from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import DefaultsOptionalModelForm

from .models import (
    EducationalStage,
    Grade,
    GradeSubject,
    Group,
    GroupSchedule,
    Instructor,
    Subject,
)


class StageForm(DefaultsOptionalModelForm):
    class Meta:
        model = EducationalStage
        fields = ["name", "name_ar", "code", "order", "is_active"]


class GradeForm(DefaultsOptionalModelForm):
    class Meta:
        model = Grade
        fields = ["stage", "name", "name_ar", "code", "order", "is_active"]


class SubjectForm(DefaultsOptionalModelForm):
    class Meta:
        model = Subject
        fields = ["name", "name_ar", "code", "color", "is_active"]


class GradeSubjectForm(DefaultsOptionalModelForm):
    class Meta:
        model = GradeSubject
        fields = ["grade", "subject", "default_monthly_fee", "is_active"]


class InstructorForm(DefaultsOptionalModelForm):
    class Meta:
        model = Instructor
        fields = ["user", "full_name", "phone", "subjects", "is_active", "notes"]


class GroupForm(DefaultsOptionalModelForm):
    class Meta:
        model = Group
        fields = [
            "grade_subject",
            "name",
            "name_ar",
            "code",
            "instructor",
            "capacity",
            "monthly_fee",
            "academic_year",
            "status",
            "default_late_after_minutes",
            "notes",
        ]

    def clean(self):
        cleaned = super().clean()
        offering = cleaned.get("grade_subject")
        # A new group inherits the offering's fee unless one was supplied.
        if offering and not cleaned.get("monthly_fee"):
            cleaned["monthly_fee"] = offering.default_monthly_fee
        if self.instance.pk and offering and self.instance.grade_subject_id != offering.pk:
            # ``lessons`` arrives in Phase 7; until then there is nothing to protect.
            if hasattr(self.instance, "lessons") and self.instance.lessons.exists():
                raise forms.ValidationError(
                    {"grade_subject": _("لا يمكن تغيير المادة/الصف بعد إنشاء حصص للمجموعة.")}
                )
        return cleaned


class GroupScheduleForm(DefaultsOptionalModelForm):
    class Meta:
        model = GroupSchedule
        fields = ["group", "weekday", "start_time", "end_time", "room", "is_active"]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if start and end and end <= start:
            raise forms.ValidationError({"end_time": _("وقت الانتهاء يجب أن يكون بعد وقت البدء.")})
        return cleaned
