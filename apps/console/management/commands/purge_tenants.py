"""Delete archived clients whose retention window has closed (TASK-116).

Never on the same day a client is archived: the console sets ``purge_after``
from the plan's retention days, and this command only touches rows past that
date. Deleting a client is the one operation with no undo, so it is deliberately
two decisions separated by weeks.
"""

from django.apps import apps as django_apps
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.tenancy.base import TenantOwnedModel
from apps.tenancy.constants import PlatformAction, TenantStatus
from apps.tenancy.models import Tenant
from apps.tenancy.platform_audit import record


class Command(BaseCommand):
    help = "Permanently delete archived clients past their purge date."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--slug", help="Purge one client (still respects purge_after).")

    def handle(self, *args, **options):
        due = Tenant.objects.filter(
            status=TenantStatus.ARCHIVED,
            purge_after__isnull=False,
            purge_after__lt=timezone.now(),
        )
        if options["slug"]:
            due = due.filter(slug=options["slug"])

        if not due.exists():
            self.stdout.write("Nothing due for purge.")
            return

        for tenant in due:
            self.stdout.write(f"{tenant.slug}: archived {tenant.archived_at:%Y-%m-%d}")
            if options["dry_run"]:
                continue
            counts = self._purge(tenant)
            self.stdout.write(self.style.WARNING(f"  purged {sum(counts.values())} rows"))

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("dry run — nothing was deleted"))

    @transaction.atomic
    def _purge(self, tenant) -> dict:
        """Delete one client's rows, leaves first.

        Every FK between domain models is ``PROTECT`` on purpose, so nothing
        cascades by accident — which means the deletion order has to be worked
        out rather than assumed. Repeating until a pass makes no progress
        removes leaves, then their parents, and so on, without hard-coding a
        dependency order that a future model would silently invalidate.
        """
        models = [m for m in django_apps.get_models() if issubclass(m, TenantOwnedModel)]
        counts: dict[str, int] = {}
        remaining = models

        while remaining:
            blocked = []
            progressed = False
            for model in remaining:
                try:
                    with transaction.atomic():
                        deleted, _detail = model.all_tenants.filter(tenant=tenant).delete()
                except ProtectedError:
                    blocked.append(model)
                    continue
                counts[model._meta.label_lower] = deleted
                progressed = True
            if not progressed:
                raise RuntimeError(
                    f"{tenant.slug}: cannot delete "
                    f"{[m._meta.label_lower for m in blocked]} — circular protection?"
                )
            remaining = blocked

        from apps.accounts.models import User

        User.all_tenants.filter(tenant=tenant).delete()
        tenant.domains.all().delete()

        slug = tenant.slug
        record(
            PlatformAction.TENANT_PURGED,
            tenant=None,
            object_repr=slug,
            changes={"slug": slug, "counts": counts},
        )
        tenant.delete()
        return counts
