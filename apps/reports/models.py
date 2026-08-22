from django.db import models
from django.utils.translation import gettext_lazy as _


class ReportPermission(models.Model):
    """Permission holder only — no table is created.

    Reports read from other apps' models, but they still need their own
    permissions to appear in the RBAC matrix.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("view_reports", _("عرض التقارير")),
            ("view_financial_reports", _("عرض التقارير المالية")),
            ("export_reports", _("تصدير التقارير")),
        ]
        verbose_name = _("صلاحيات التقارير")
