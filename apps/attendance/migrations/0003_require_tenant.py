"""Make `tenant` NOT NULL, now that every row carries one (TASK-103).

Depends on tenancy.0002_bootstrap_default_tenant: the backfill has to have run,
or this fails with an opaque IntegrityError instead of a clear one.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenancy", "0002_bootstrap_default_tenant"),
        ("attendance", "0002_add_tenant"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attendance",
            name="tenant",
            field=models.ForeignKey(
                editable=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tenancy.tenant",
                verbose_name="العميل",
            ),
        ),
        migrations.AlterField(
            model_name="attendanceevent",
            name="tenant",
            field=models.ForeignKey(
                editable=False,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tenancy.tenant",
                verbose_name="العميل",
            ),
        ),
    ]
