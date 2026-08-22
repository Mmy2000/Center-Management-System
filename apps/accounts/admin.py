from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "full_name", "role", "is_active", "last_login")
    list_filter = ("role", "is_active", "is_superuser")
    search_fields = ("username", "full_name", "phone")
    fieldsets = DjangoUserAdmin.fieldsets + (
        (
            _("بيانات السنتر"),
            {"fields": ("full_name", "phone", "role", "must_change_password", "last_login_ip")},
        ),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        (_("بيانات السنتر"), {"fields": ("full_name", "phone", "role")}),
    )
    readonly_fields = ("last_login_ip",)
