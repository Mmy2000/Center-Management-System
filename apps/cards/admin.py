from django.contrib import admin

from .models import CardAssignment, StudentCard


@admin.register(StudentCard)
class StudentCardAdmin(admin.ModelAdmin):
    list_display = (
        "card_number",
        "masked_token",
        "status",
        "current_student",
        "batch",
        "issued_at",
    )
    list_filter = ("status", "batch")
    search_fields = ("card_number",)
    readonly_fields = ("qr_token", "issued_at", "lost_at", "disabled_at", "replaced_at")

    @admin.display(description="QR")
    def masked_token(self, obj):
        return obj.masked_token


@admin.register(CardAssignment)
class CardAssignmentAdmin(admin.ModelAdmin):
    """Read-only: the card timeline is evidence."""

    list_display = ("card", "student", "assigned_at", "released_at", "release_reason")
    list_filter = ("release_reason",)
    search_fields = ("card__card_number", "student__full_name", "student__student_code")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
