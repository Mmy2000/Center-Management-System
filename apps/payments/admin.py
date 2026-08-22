from django.contrib import admin

from .models import MonthlyCharge, Payment


@admin.register(MonthlyCharge)
class MonthlyChargeAdmin(admin.ModelAdmin):
    list_display = (
        "student", "grade_subject", "billing_month", "amount_due",
        "discount_amount", "total_paid", "balance", "status",
    )
    list_filter = ("status", "billing_month", "grade_subject__subject")
    search_fields = ("student__full_name", "student__student_code")
    readonly_fields = ("total_paid", "balance", "status")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Money is append-only: no add, no change, no delete."""

    list_display = (
        "receipt_number", "student", "kind", "amount", "method", "paid_at", "collected_by",
    )
    list_filter = ("kind", "method", "paid_at")
    search_fields = ("receipt_number", "student__full_name", "student__student_code")
    date_hierarchy = "paid_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
