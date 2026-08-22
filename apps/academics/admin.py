from django.contrib import admin

from .models import (
    EducationalStage,
    Grade,
    GradeSubject,
    Group,
    GroupSchedule,
    Instructor,
    Subject,
)


@admin.register(EducationalStage)
class EducationalStageAdmin(admin.ModelAdmin):
    list_display = ("name", "name_ar", "code", "order", "is_active")
    list_editable = ("order", "is_active")


@admin.register(Grade)
class GradeAdmin(admin.ModelAdmin):
    list_display = ("name", "stage", "code", "order", "is_active")
    list_filter = ("stage", "is_active")


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "name_ar", "code", "is_active")
    list_filter = ("is_active",)


@admin.register(GradeSubject)
class GradeSubjectAdmin(admin.ModelAdmin):
    list_display = ("grade", "subject", "default_monthly_fee", "is_active")
    list_filter = ("grade__stage", "subject", "is_active")


@admin.register(Instructor)
class InstructorAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "user", "is_active")
    list_filter = ("is_active",)
    search_fields = ("full_name", "phone")


class GroupScheduleInline(admin.TabularInline):
    model = GroupSchedule
    extra = 1


@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "name",
        "grade_subject",
        "instructor",
        "capacity",
        "monthly_fee",
        "status",
    )
    list_filter = ("status", "grade_subject__grade__stage", "grade_subject__subject")
    search_fields = ("name", "code")
    inlines = [GroupScheduleInline]
