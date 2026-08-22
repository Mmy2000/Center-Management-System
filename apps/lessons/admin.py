from django.contrib import admin

from .models import Lesson


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("group", "lesson_date", "scheduled_start", "status", "expected_students")
    list_filter = ("status", "lesson_date", "group__grade_subject__subject")
    date_hierarchy = "lesson_date"
    readonly_fields = ("lesson_date", "actual_start_at", "actual_end_at")
