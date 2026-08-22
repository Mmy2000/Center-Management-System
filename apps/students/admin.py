from django.contrib import admin

from .models import Student


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("student_code", "full_name", "grade", "status", "guardian_phone")
    list_filter = ("status", "grade__stage", "grade")
    search_fields = ("student_code", "full_name", "search_name", "guardian_phone", "phone")
    readonly_fields = ("student_code", "search_name", "created_at", "updated_at")
