"""Turn the existing single-center installation into tenant #1 (TASK-103).

Runs after every ``*_add_tenant`` migration has added the nullable column, and
before every ``*_require_tenant`` migration makes it NOT NULL. Splitting it that
way is what keeps each step reversible: this one only writes rows, and reversing
it clears them again.

The tenant is built from what the installation already knows about itself — the
``center.name`` policy override — so the first client keeps its own name rather
than being called "default". Its primary host comes from ``DEFAULT_TENANT_HOST``
(env), which is required only when there is data to migrate: a fresh database
has nothing to bootstrap and this migration does nothing at all.
"""

import os

from django.db import migrations

# Every table that gained a `tenant` column, in an order that does not matter
# (the backfill is a blind UPDATE) but is grouped for readability.
TENANT_OWNED = [
    ("core", "Setting"),
    ("core", "Sequence"),
    ("academics", "EducationalStage"),
    ("academics", "Grade"),
    ("academics", "Subject"),
    ("academics", "GradeSubject"),
    ("academics", "Instructor"),
    ("academics", "Group"),
    ("academics", "GroupSchedule"),
    ("students", "Student"),
    ("students", "StudentGroupAssignment"),
    ("cards", "StudentCard"),
    ("cards", "CardAssignment"),
    ("lessons", "Lesson"),
    ("attendance", "Attendance"),
    ("attendance", "AttendanceEvent"),
    ("payments", "MonthlyCharge"),
    ("payments", "Payment"),
]

# Nullable tenant columns: backfilled the same way, but legitimately stay null
# for rows that belong to no center.
NULLABLE = [
    ("core", "AuditLog"),
    ("accounts", "User"),
]

DEFAULT_SLUG = "default"
FALLBACK_HOST = "localhost"


def _existing_rows(apps):
    """Whether this database already holds a center's data."""
    for app_label, model_name in TENANT_OWNED:
        if apps.get_model(app_label, model_name).objects.exists():
            return True
    return apps.get_model("accounts", "User").objects.exists()


def _center_name(apps) -> str:
    """The center's own name, from the policy override it already stores."""
    Setting = apps.get_model("core", "Setting")
    row = Setting.objects.filter(key="center.name").first()
    if row is not None and isinstance(row.value, str) and row.value.strip():
        return row.value.strip()[:150]
    return "السنتر"


def bootstrap(apps, schema_editor):
    Tenant = apps.get_model("tenancy", "Tenant")
    Domain = apps.get_model("tenancy", "Domain")
    Plan = apps.get_model("tenancy", "Plan")
    PlanFeature = apps.get_model("tenancy", "PlanFeature")

    if Tenant.objects.exists():
        return  # already bootstrapped
    if not _existing_rows(apps):
        return  # fresh database — provision_tenant creates the first client

    # The historical model has no access to features.FEATURES, and hard-coding
    # the catalogue here would freeze it at today's contents. An empty feature
    # set plus no limits is the honest "everything the code allows" plan for the
    # installation that predates plans; `seed_plans` fills in the real ones.
    plan, _ = Plan.objects.get_or_create(
        slug="migrated",
        defaults={
            "name": "الباقة الأصلية",
            "description": "أُنشئت تلقائيًا عند تحويل النظام إلى متعدد العملاء.",
            "is_public": False,
            "sort_order": 0,
            "retention_days": 90,
        },
    )
    PlanFeature.objects.filter(plan=plan).delete()

    tenant = Tenant.objects.create(
        slug=DEFAULT_SLUG,
        name=_center_name(apps),
        status="ACTIVE",
        plan=plan,
        timezone="Africa/Cairo",
        language="ar",
    )
    Domain.objects.create(
        tenant=tenant,
        host=os.environ.get("DEFAULT_TENANT_HOST", FALLBACK_HOST).strip().lower(),
        is_primary=True,
        is_custom=False,
    )

    counts = {}
    for app_label, model_name in TENANT_OWNED:
        model = apps.get_model(app_label, model_name)
        counts[f"{app_label}.{model_name}"] = model.objects.update(tenant=tenant)

    # AuditLog rows all predate the platform, so they belong to this center.
    apps.get_model("core", "AuditLog").objects.update(tenant=tenant)

    # Every existing user is this center's staff, except a superuser, who
    # becomes platform staff — they are the person running the migration.
    User = apps.get_model("accounts", "User")
    User.objects.filter(is_superuser=False).update(tenant=tenant)
    User.objects.filter(is_superuser=True).update(tenant=None, is_platform_staff=True)

    # A row that survived with no tenant would fail the NOT NULL migration that
    # follows with an opaque IntegrityError; say which table it was instead.
    for app_label, model_name in TENANT_OWNED:
        model = apps.get_model(app_label, model_name)
        orphans = model.objects.filter(tenant__isnull=True).count()
        if orphans:
            raise RuntimeError(
                f"{app_label}.{model_name}: {orphans} rows still have no tenant "
                f"after the backfill — refusing to continue."
            )


def unbootstrap(apps, schema_editor):
    """Reverse: clear the stamps and drop the tenant that was created here."""
    Tenant = apps.get_model("tenancy", "Tenant")
    tenant = Tenant.objects.filter(slug=DEFAULT_SLUG).first()
    if tenant is None:
        return

    for app_label, model_name in TENANT_OWNED + NULLABLE:
        apps.get_model(app_label, model_name).objects.filter(tenant=tenant).update(tenant=None)

    apps.get_model("accounts", "User").objects.update(is_platform_staff=False)
    apps.get_model("tenancy", "Domain").objects.filter(tenant=tenant).delete()
    tenant.delete()
    apps.get_model("tenancy", "Plan").objects.filter(slug="migrated").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tenancy", "0001_initial"),
        ("core", "0004_add_tenant"),
        ("accounts", "0002_add_tenant"),
        ("academics", "0002_add_tenant"),
        ("students", "0003_add_tenant"),
        ("cards", "0002_add_tenant"),
        ("lessons", "0002_add_tenant"),
        ("attendance", "0002_add_tenant"),
        ("payments", "0002_add_tenant"),
    ]

    operations = [
        migrations.RunPython(bootstrap, unbootstrap),
    ]
