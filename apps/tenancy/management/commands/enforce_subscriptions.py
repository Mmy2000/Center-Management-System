"""Let a subscription end without you being awake (docs/10 §N.10, TASK-110).

    TRIAL     past trial_ends_at  ->  PAST_DUE
    ACTIVE    past expires_at     ->  PAST_DUE   (and grace_until is set)
    PAST_DUE  past grace_until    ->  SUSPENDED

Run daily from cron or Celery beat. Idempotent: a second run the same day
changes nothing, because each transition moves the tenant out of the state the
rule matches on.

Two rules it deliberately does **not** break:

* A center an operator suspended by hand is never auto-resumed. This command
  only ever moves clients *towards* suspension; coming back is a decision.
* Nothing is deleted, at any step. A suspended center that pays resumes
  byte for byte.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.tenancy.constants import TenantStatus
from apps.tenancy.models import Tenant

#: How long a client keeps working after their subscription lapses. A late
#: invoice is a conversation, not a reason to stop a center taking attendance
#: that morning — see `Tenant.is_operational`.
DEFAULT_GRACE_DAYS = 14


class Command(BaseCommand):
    help = "Move subscriptions past their dates: TRIAL/ACTIVE -> PAST_DUE -> SUSPENDED."

    def add_arguments(self, parser):
        parser.add_argument(
            "--grace-days",
            type=int,
            default=DEFAULT_GRACE_DAYS,
            help=f"Days between lapsing and suspension (default {DEFAULT_GRACE_DAYS}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change and write nothing.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        grace = timezone.timedelta(days=options["grace_days"])
        dry_run = options["dry_run"]
        moved = {"trial_lapsed": 0, "subscription_lapsed": 0, "suspended": 0}

        with transaction.atomic():
            for tenant in Tenant.objects.select_related("plan").filter(
                status=TenantStatus.TRIAL, trial_ends_at__lt=now
            ):
                moved["trial_lapsed"] += 1
                self._move(
                    tenant,
                    TenantStatus.PAST_DUE,
                    "انتهت الفترة التجريبية",
                    dry_run,
                    grace_until=now + grace,
                )

            for tenant in Tenant.objects.select_related("plan").filter(
                status=TenantStatus.ACTIVE, expires_at__lt=now
            ):
                moved["subscription_lapsed"] += 1
                self._move(
                    tenant,
                    TenantStatus.PAST_DUE,
                    "انتهى الاشتراك",
                    dry_run,
                    grace_until=now + grace,
                )

            for tenant in Tenant.objects.select_related("plan").filter(
                status=TenantStatus.PAST_DUE, grace_until__lt=now
            ):
                moved["suspended"] += 1
                self._move(tenant, TenantStatus.SUSPENDED, "انتهت فترة السماح دون سداد", dry_run)

        for key, count in moved.items():
            self.stdout.write(f"{key}: {count}")
        if dry_run:
            self.stdout.write(self.style.WARNING("dry run — nothing was written"))
        else:
            self.stdout.write(self.style.SUCCESS("subscriptions enforced"))

    def _move(self, tenant, status, reason, dry_run, *, grace_until=None):
        self.stdout.write(f"  {tenant.slug}: {tenant.status} -> {status} ({reason})")
        if dry_run:
            return
        if grace_until is not None and tenant.grace_until is None:
            tenant.grace_until = grace_until
            tenant.save(update_fields=["grace_until", "updated_at"])
        tenant.transition_to(status, reason=reason, automatic=True)
