"""Create the three plans the product is sold as (TASK-090).

Idempotent: safe to run on every deploy. It updates limits and feature sets in
place, so raising a plan's student cap is a code change plus a re-run rather
than a manual UPDATE on production.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.tenancy.features import FEATURES
from apps.tenancy.models import Plan, PlanFeature

ALL_KEYS = tuple(spec.key for spec in FEATURES)

# Keys a plan below "full" does not include. Everything else is on.
BASIC_EXCLUDES = {
    "attendance.offline_queue",
    "payments.enforce_on_attendance",
    "payments.receipt_pdf",
    "payments.refunds",
    "reports.export_excel",
    "reports.export_pdf",
    "reports.financial",
    "users.management",
    "settings.editor",
}
STANDARD_EXCLUDES = {
    "attendance.offline_queue",
    "payments.enforce_on_attendance",
}

PLANS = [
    {
        "slug": "basic",
        "name": "الباقة الأساسية",
        "description": "سنتر صغير: حضور وطلاب ومجموعات، بدون الجانب المالي المتقدم.",
        "sort_order": 10,
        "max_students": 300,
        "max_users": 5,
        "max_groups": 30,
        "max_cards": 500,
        "storage_mb": 512,
        "excludes": BASIC_EXCLUDES,
    },
    {
        "slug": "standard",
        "name": "الباقة المتوسطة",
        "description": "سنتر متوسط: كل شيء عدا الخصائص التي تُفعّل عند الطلب.",
        "sort_order": 20,
        "max_students": 1500,
        "max_users": 20,
        "max_groups": 150,
        "max_cards": 2500,
        "storage_mb": 4096,
        "excludes": STANDARD_EXCLUDES,
    },
    {
        "slug": "full",
        "name": "الباقة الكاملة",
        "description": "كل الخصائص بلا حدود.",
        "sort_order": 30,
        "max_students": None,
        "max_users": None,
        "max_groups": None,
        "max_cards": None,
        "storage_mb": None,
        "excludes": set(),
    },
]


class Command(BaseCommand):
    help = "Create or update the standard plans and their feature sets."

    @transaction.atomic
    def handle(self, *args, **options):
        for spec in PLANS:
            # Copy: PLANS is module state, and a command that consumed it would
            # work once per process and fail on the second call — which is
            # exactly what "idempotent" is supposed to rule out.
            fields = dict(spec)
            excludes = fields.pop("excludes")
            slug = fields.pop("slug")
            plan, created = Plan.objects.update_or_create(slug=slug, defaults=fields)

            wanted = {key for key in ALL_KEYS if key not in excludes}
            current = set(plan.features.values_list("feature_key", flat=True))

            PlanFeature.objects.filter(plan=plan, feature_key__in=current - wanted).delete()
            PlanFeature.objects.bulk_create(
                [PlanFeature(plan=plan, feature_key=key) for key in sorted(wanted - current)]
            )

            verb = "created" if created else "updated"
            self.stdout.write(f"{verb} plan {slug!r}: {len(wanted)} features")

        self.stdout.write(self.style.SUCCESS(f"{len(PLANS)} plans ready."))
