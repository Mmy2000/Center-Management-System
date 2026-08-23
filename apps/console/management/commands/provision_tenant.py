"""Create a client from the shell (TASK-113).

The same service the wizard calls, so the two cannot drift apart. This is the
path for the *first* client on a fresh install, when there is no console
account yet to log in with.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.console import services
from apps.tenancy.models import Plan, Tenant


class Command(BaseCommand):
    help = "Provision a new client: tenant, domain, roles, owner account."

    def add_arguments(self, parser):
        parser.add_argument("--slug", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--plan", default="full", help="Plan slug (default: full).")
        parser.add_argument("--base-domain", default=None)
        parser.add_argument("--owner-username", default="admin")
        parser.add_argument("--owner-name", default="")
        parser.add_argument("--owner-email", default="")
        parser.add_argument("--owner-phone", default="")
        parser.add_argument("--trial-days", type=int, default=30)
        parser.add_argument("--academics", action="store_true", help="Seed stages and grades.")
        parser.add_argument("--demo", action="store_true", help="Seed demo data too.")

    def handle(self, *args, **options):
        if Tenant.objects.filter(slug=options["slug"]).exists():
            raise CommandError(f"A client with slug {options['slug']!r} already exists.")
        try:
            plan = Plan.objects.get(slug=options["plan"])
        except Plan.DoesNotExist as exc:
            raise CommandError(
                f"No plan {options['plan']!r}. Run `manage.py seed_plans` first."
            ) from exc

        result = services.provision_tenant(
            slug=options["slug"],
            name=options["name"],
            plan=plan,
            base_domain=options["base_domain"] or settings.TENANT_BASE_DOMAIN,
            owner_username=options["owner_username"],
            owner_name=options["owner_name"],
            owner_email=options["owner_email"],
            owner_phone=options["owner_phone"],
            trial_days=options["trial_days"] or None,
            seed_academics=options["academics"],
            demo=options["demo"],
        )

        tenant = result["tenant"]
        self.stdout.write(self.style.SUCCESS(f"Created {tenant.name} ({tenant.slug})"))
        self.stdout.write(f"  site:     https://{tenant.primary_host}/")
        self.stdout.write(f"  username: {result['owner'].username}")
        self.stdout.write(f"  password: {result['password']}")
        self.stdout.write(
            self.style.WARNING("  This password is shown once and is not stored anywhere.")
        )
