"""Per-tenant request limits and a way to shut one client's traffic off.

Additive in both halves. The new table is empty on arrival and absence of a row
*is* the default — unlimited and unblocked — so every existing center keeps
behaving exactly as it did with no data migration and nothing to backfill.

The ``PlatformAuditLog.action`` alter only widens a choice list; the column is
unchanged, so there is no table rewrite on either Postgres or SQLite.
"""


import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0003_plan_pricing"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="platformauditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("TENANT_CREATED", "إنشاء عميل"),
                    ("TENANT_UPDATED", "تعديل عميل"),
                    ("TENANT_SUSPENDED", "إيقاف عميل"),
                    ("TENANT_RESUMED", "إعادة تفعيل عميل"),
                    ("TENANT_ARCHIVED", "أرشفة عميل"),
                    ("TENANT_PURGED", "حذف بيانات عميل"),
                    ("TENANT_EXPORTED", "تصدير بيانات عميل"),
                    ("PLAN_CHANGED", "تغيير الباقة"),
                    ("PLAN_CREATED", "إنشاء باقة"),
                    ("PLAN_UPDATED", "تعديل باقة"),
                    ("FEATURE_CHANGED", "تغيير خاصية"),
                    ("LIMIT_CHANGED", "تغيير حد"),
                    ("DOMAIN_ADDED", "إضافة نطاق"),
                    ("DOMAIN_REMOVED", "حذف نطاق"),
                    ("IMPERSONATION_STARTED", "بدء الدخول نيابةً"),
                    ("IMPERSONATION_ENDED", "إنهاء الدخول نيابةً"),
                    ("SUBSCRIPTION_TRANSITION", "تغيير حالة الاشتراك"),
                    ("TRAFFIC_LIMITED", "ضبط حدود الطلبات"),
                    ("TRAFFIC_BLOCKED", "حظر حركة عميل"),
                    ("TRAFFIC_UNBLOCKED", "رفع حظر الحركة"),
                ],
                max_length=40,
                verbose_name="الإجراء",
            ),
        ),
        migrations.CreateModel(
            name="TenantRatePolicy",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "blocked",
                    models.BooleanField(
                        default=False,
                        help_text="يرفض كل الطلبات فورًا. لا يُحذف شيء، ورفع الحظر يعيد الخدمة في الحال.",
                        verbose_name="محظور",
                    ),
                ),
                ("blocked_reason", models.TextField(blank=True, verbose_name="سبب الحظر")),
                (
                    "blocked_at",
                    models.DateTimeField(blank=True, null=True, verbose_name="تاريخ الحظر"),
                ),
                (
                    "max_rps",
                    models.PositiveIntegerField(
                        blank=True,
                        help_text="فارغ = بلا حد.",
                        null=True,
                        validators=[django.core.validators.MinValueValidator(1)],
                        verbose_name="أقصى عدد طلبات في الثانية",
                    ),
                ),
                (
                    "max_rpm",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(1)],
                        verbose_name="أقصى عدد طلبات في الدقيقة",
                    ),
                ),
                (
                    "max_rph",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        validators=[django.core.validators.MinValueValidator(1)],
                        verbose_name="أقصى عدد طلبات في الساعة",
                    ),
                ),
                (
                    "burst",
                    models.PositiveIntegerField(
                        default=0,
                        help_text="طلبات إضافية فوق الحد لكل ثانية، لاستيعاب دفعة مسح متزامنة.",
                        verbose_name="سماح الذروة",
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=200, verbose_name="ملاحظة")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "tenant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rate_policy",
                        to="tenancy.tenant",
                        verbose_name="العميل",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tenant_rate_policy_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "سياسة حركة عميل",
                "verbose_name_plural": "سياسات حركة العملاء",
                "ordering": ["tenant"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("burst", 0), ("max_rps__isnull", False), _connector="OR"
                        ),
                        name="ck_burst_needs_rps",
                    )
                ],
            },
        ),
    ]
