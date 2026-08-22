from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _


class Role(models.TextChoices):
    """Operational roles (docs/05 §G.5).

    ``role`` is a convenience denormalization used for UI and seeding; actual
    authorization is always checked against Django permissions, which
    ``seed_roles`` attaches to a matching auth.Group.
    """

    SUPER_ADMIN = "SUPER_ADMIN", _("مدير النظام")
    CENTER_ADMIN = "CENTER_ADMIN", _("مدير السنتر")
    INSTRUCTOR = "INSTRUCTOR", _("مدرّس")
    CASHIER = "CASHIER", _("أمين الخزنة")
    SCAN_OPERATOR = "SCAN_OPERATOR", _("مشغّل المسح")


class User(AbstractUser):
    full_name = models.CharField(_("الاسم الكامل"), max_length=150, blank=True)
    phone = models.CharField(_("رقم الهاتف"), max_length=20, blank=True)
    role = models.CharField(
        _("الدور"), max_length=30, choices=Role.choices, default=Role.SCAN_OPERATOR
    )
    must_change_password = models.BooleanField(_("يجب تغيير كلمة المرور"), default=False)
    last_login_ip = models.GenericIPAddressField(_("آخر IP"), null=True, blank=True)

    class Meta:
        verbose_name = _("مستخدم")
        verbose_name_plural = _("المستخدمون")
        ordering = ["username"]

    def __str__(self):
        return self.full_name or self.get_username()

    @property
    def display_name(self):
        return self.full_name or self.get_username()

    @property
    def is_super_admin(self):
        return self.role == Role.SUPER_ADMIN or self.is_superuser

    @property
    def is_instructor(self):
        return self.role == Role.INSTRUCTOR
