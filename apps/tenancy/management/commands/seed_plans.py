"""Create the three plans the product starts with (TASK-090).

These are a *starting point*, not the truth: plans are editable from the console
(name, prices, discount, limits, feature grants, availability), and this command
only fills in what a fresh install needs to have something to sell.

Idempotent, and deliberately conservative about it: a re-run refreshes the
feature grants and the limits, but never overwrites a price or a discount an
operator has since set by hand. A deploy that silently reset every client's
agreed price would be a very bad afternoon.
"""

from decimal import Decimal

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

#: Opening prices, in EGP. The yearly figure is ten months' worth, so a year
#: costs about 17% less than paying monthly — a discount an operator can then
#: change per plan without touching this file again.
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
        "monthly_price": Decimal("500.00"),
        "yearly_price": Decimal("5000.00"),
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
        "monthly_price": Decimal("1200.00"),
        "yearly_price": Decimal("12000.00"),
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
        "monthly_price": Decimal("2500.00"),
        "yearly_price": Decimal("25000.00"),
        "excludes": set(),
    },
]

#: Fields a re-run always refreshes. Prices and discounts are absent on purpose:
#: they belong to the operator, not the repository. A price that is still NULL
#: is filled in once (see below) — an absence is not a decision.
REFRESHED_FIELDS = {
    "name",
    "description",
    "sort_order",
    "max_students",
    "max_users",
    "max_groups",
    "max_cards",
    "storage_mb",
}


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

            plan = Plan.objects.filter(slug=slug).first()
            created = plan is None
            if created:
                plan = Plan.objects.create(slug=slug, **fields)
            else:
                # Refresh only what the repository owns. A price an operator
                # negotiated is not ours to reset on the next deploy.
                for name in REFRESHED_FIELDS:
                    if name in fields:
                        setattr(plan, name, fields[name])
                # A *missing* price is different from a chosen one: a plan that
                # predates pricing has no figure to protect, so fill it in once.
                # After that the operator owns it.
                for name in ("monthly_price", "yearly_price"):
                    if getattr(plan, name) is None and fields.get(name) is not None:
                        setattr(plan, name, fields[name])
                plan.save()

            wanted = {key for key in ALL_KEYS if key not in excludes}
            current = set(plan.features.values_list("feature_key", flat=True))

            PlanFeature.objects.filter(plan=plan, feature_key__in=current - wanted).delete()
            PlanFeature.objects.bulk_create(
                [PlanFeature(plan=plan, feature_key=key) for key in sorted(wanted - current)]
            )

            verb = "created" if created else "updated"
            self.stdout.write(f"{verb} plan {slug!r}: {len(wanted)} features")

        self.stdout.write(self.style.SUCCESS(f"{len(PLANS)} plans ready."))
