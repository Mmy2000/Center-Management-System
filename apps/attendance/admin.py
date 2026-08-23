from django.contrib import admin

from .models import Attendance, AttendanceEvent


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    """Read-only in the admin: corrections must go through the audited service."""

    list_display = (
        "student",
        "lesson",
        "state",
        "status",
        "attendance_type",
        "assigned_group",
        "attended_group",
        "check_in_at",
        "is_manual",
    )
    list_filter = ("state", "status", "attendance_type", "is_manual", "lesson__lesson_date")
    search_fields = ("student__full_name", "student__student_code")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AttendanceEvent)
class AttendanceEventAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "lesson",
        "student",
        "event_type",
        "result_code",
        "device_id",
        "latency_ms",
    )
    list_filter = ("event_type", "result_code")
    search_fields = ("student__full_name", "device_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
