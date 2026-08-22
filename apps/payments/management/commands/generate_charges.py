"""Issue the month's charges (TASK-062). Idempotent — safe to re-run."""

from django.core.management.base import BaseCommand

from apps.payments.services import generate_monthly_charges


class Command(BaseCommand):
    help = "Generate monthly charges for every active assignment."

    def add_arguments(self, parser):
        parser.add_argument("month", help="YYYY-MM")
        parser.add_argument("--group", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        group = None
        if options["group"]:
            from apps.academics.models import Group

            group = Group.objects.get(pk=options["group"])

        summary = generate_monthly_charges(
            options["month"], group=group, dry_run=options["dry_run"]
        )
        if options.get("verbosity", 1) >= 1:
            self.stdout.write(
                f"{summary['billing_month']}: created={summary['created']} "
                f"existing={summary['existing']} students={summary['students']} "
                f"total={summary['total_amount']}"
                + (" (dry run)" if summary["dry_run"] else "")
            )
