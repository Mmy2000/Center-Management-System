"""Nightly drift check (TASK-065): stored total_paid vs the ledger."""

from django.core.management.base import BaseCommand

from apps.payments.services import recalculate_charges


class Command(BaseCommand):
    help = "Re-derive total_paid and status from the payment ledger."

    def add_arguments(self, parser):
        parser.add_argument("--month", default=None, help="YYYY-MM")
        parser.add_argument(
            "--check-only", action="store_true", help="Report drift, change nothing."
        )

    def handle(self, *args, **options):
        result = recalculate_charges(options["month"], fix=not options["check_only"])
        if result["drift"]:
            self.stdout.write(
                self.style.WARNING(
                    f"drift on {len(result['drift'])} charge(s): {result['drift'][:5]}"
                )
            )
        elif options.get("verbosity", 1) >= 1:
            self.stdout.write(self.style.SUCCESS(f"checked {result['checked']} charges, no drift"))
