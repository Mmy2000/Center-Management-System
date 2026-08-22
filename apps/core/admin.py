from django.contrib import admin

from .models import AuditLog, Setting


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "updated_at", "updated_by")
    search_fields = ("key",)
    readonly_fields = ("updated_at",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only by design: the audit trail is evidence, not data entry."""

    list_display = ("created_at", "actor", "action", "object_repr", "reason")
    list_filter = ("action", "created_at")
    search_fields = ("object_repr", "reason")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
