from django.contrib.auth.models import AbstractUser
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from .managers import AllTenantsUserManager, TenantUserManager


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
    """A member of one center's staff — or, with a null tenant, of yours.

    ``username`` loses its *global* uniqueness here: two centers must be able to
    both have an "admin", and telling one of them "that name is taken" would
    leak the other's existence. Uniqueness becomes per-tenant, plus a partial
    constraint covering platform staff, who have no tenant to be unique within.
    """

    # Redeclared from AbstractUser purely to drop unique=True; the constraints
    # in Meta take over. Everything else matches the parent field exactly.
    username = models.CharField(
        _("اسم المستخدم"),
        max_length=150,
        help_text=_("150 حرفًا على الأكثر: حروف وأرقام و @ . + - _ فقط."),
        validators=[UnicodeUsernameValidator()],
    )
    tenant = models.ForeignKey(
        "tenancy.Tenant",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
        verbose_name=_("العميل"),
        help_text=_("فارغ = من فريق المنصة، خارج كل السناتر."),
    )
    is_platform_staff = models.BooleanField(
        _("من فريق المنصة"),
        default=False,
        help_text=_("يفتح لوحة تحكم المنصة. ليس له أي معنى داخل سنتر."),
    )
    full_name = models.CharField(_("الاسم الكامل"), max_length=150, blank=True)
    phone = models.CharField(_("رقم الهاتف"), max_length=20, blank=True)
    role = models.CharField(
        _("الدور"), max_length=30, choices=Role.choices, default=Role.SCAN_OPERATOR
    )
    must_change_password = models.BooleanField(_("يجب تغيير كلمة المرور"), default=False)
    last_login_ip = models.GenericIPAddressField(_("آخر IP"), null=True, blank=True)

    objects = TenantUserManager()
    all_tenants = AllTenantsUserManager()

    class Meta:
        verbose_name = _("مستخدم")
        verbose_name_plural = _("المستخدمون")
        ordering = ["username"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "username"],
                name="uq_username_per_tenant",
            ),
            # Platform staff have no tenant to be unique within, and NULLs do
            # not collide in a composite unique index — so they need their own.
            models.UniqueConstraint(
                fields=["username"],
                condition=models.Q(tenant__isnull=True),
                name="uq_platform_username",
            ),
        ]
        indexes = [models.Index(fields=["tenant", "username"])]

    def __str__(self):
        return self.full_name or self.get_username()

    @property
    def display_name(self):
        return self.full_name or self.get_username()

    @property
    def is_super_admin(self):
        """The *center's* owner — never the platform operator.

        A center admin must never be a superuser, and a platform operator must
        not automatically inherit a center's powers; ``is_platform_staff`` is
        the separate flag that gates the console.
        """
        return self.role == Role.SUPER_ADMIN or self.is_superuser

    @property
    def is_instructor(self):
        return self.role == Role.INSTRUCTOR
