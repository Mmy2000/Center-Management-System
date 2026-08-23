"""Plans become editable, priced things rather than seeded constants.

`is_active` lets a plan be retired without touching the clients already on it.
The prices are *recorded*, not charged — the console shows what was agreed so a
plan change can be costed; it still issues no invoice (docs/10 §N.14).
"""

import django.core.validators
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0002_bootstrap_default_tenant"),
    ]

    operations = [
        migrations.AddField(
            model_name="plan",
            name="currency",
            field=models.CharField(default="EGP", max_length=8, verbose_name="العملة"),
        ),
        migrations.AddField(
            model_name="plan",
            name="discount_label",
            field=models.CharField(blank=True, max_length=100, verbose_name="سبب الخصم"),
        ),
        migrations.AddField(
            model_name="plan",
            name="discount_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(100),
                ],
                verbose_name="نسبة الخصم %",
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="discount_until",
            field=models.DateField(
                blank=True,
                help_text="فارغ = خصم دائم حتى تغيّره.",
                null=True,
                verbose_name="الخصم حتى",
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="is_active",
            field=models.BooleanField(
                default=True,
                help_text="إيقافها يمنع تعيينها لعملاء جدد. العملاء الحاليون عليها لا يتأثرون.",
                verbose_name="متاحة",
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="monthly_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
                verbose_name="السعر الشهري",
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="yearly_price",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                validators=[django.core.validators.MinValueValidator(0)],
                verbose_name="السعر السنوي",
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="billing_cycle",
            field=models.CharField(
                choices=[("MONTHLY", "شهري"), ("YEARLY", "سنوي"), ("NONE", "بدون")],
                default="MONTHLY",
                help_text="أي سعر من أسعار الباقة ينطبق على هذا العميل.",
                max_length=10,
                verbose_name="دورة الفوترة",
            ),
        ),
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
                ],
                max_length=40,
                verbose_name="الإجراء",
            ),
        ),
        migrations.AddConstraint(
            model_name="plan",
            constraint=models.CheckConstraint(
                condition=models.Q(("discount_percent__gte", 0), ("discount_percent__lte", 100)),
                name="ck_plan_discount_range",
            ),
        ),
    ]
