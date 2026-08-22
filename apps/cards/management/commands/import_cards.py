"""Import a vendor's pre-printed card batch (TASK-029).

Thin wrapper around apps.cards.importer — same validation as the UI upload.

CSV columns: card_number, qr_token[, batch]
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.cards.importer import import_batch
from apps.core.http import DomainError


class Command(BaseCommand):
    help = "Import pre-printed cards from a CSV file (all-or-nothing)."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--batch", default="", help="Batch label for rows without one.")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"File not found: {path}")

        try:
            # utf-8-sig tolerates the BOM Excel writes.
            result = import_batch(
                path.read_text(encoding="utf-8-sig"), batch=options["batch"]
            )
        except DomainError as exc:
            raise CommandError(exc.message) from exc

        if options.get("verbosity", 1) >= 1:
            self.stdout.write(self.style.SUCCESS(f"Imported {result['created']} cards."))
