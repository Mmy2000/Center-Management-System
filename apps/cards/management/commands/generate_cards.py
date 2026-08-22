"""Generate blank cards for a print shop (TASK-029).

Thin wrapper: the logic lives in apps.cards.importer so the UI button and this
command cannot drift apart.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.cards.importer import generate_batch
from apps.core.http import DomainError


class Command(BaseCommand):
    help = "Create N AVAILABLE cards with freshly generated QR tokens."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, required=True)
        parser.add_argument("--batch", default="")

    def handle(self, *args, **options):
        try:
            result = generate_batch(options["count"], batch=options["batch"])
        except DomainError as exc:
            raise CommandError(exc.message) from exc

        if options.get("verbosity", 1) >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Generated {result['created']} cards "
                    f"({result['first']} … {result['last']})."
                )
            )
