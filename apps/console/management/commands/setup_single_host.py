"""Prepare a one-hostname install (PythonAnywhere free tier and the like).

    python manage.py setup_single_host --host mmy.pythonanywhere.com

One hostname means one center: there is no second host to route a second client
to, and no host left over for the console — which is why the console is served
from a path there instead (``CONSOLE_PATH_PREFIX``).

What this does, all idempotently:

* points the hostname at the single center, creating it if the database is
  empty (a fresh install) or adopting the existing one (a migrated single-center
  database)
* seeds the plans
* makes sure there is a console operator to sign in as

It refuses to run on a database that already holds more than one client: that
is a multi-tenant install, and pointing its one hostname at one of them would
silently strand the others.
"""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.console import services
from apps.tenancy.constants import TenantStatus
from apps.tenancy.models import Domain, Plan, Tenant


class Command(BaseCommand):
    help = "Point a single hostname at a single center, and ensure a console operator."

    def add_arguments(self, parser):
        parser.add_argument("--host", required=True, help="e.g. mmy.pythonanywhere.com")
        parser.add_argument("--name", default="السنتر", help="The center's display name.")
        parser.add_argument("--slug", default="center", help="Used only for internal ids.")
        parser.add_argument("--operator", default="platform", help="Console username to ensure.")
        parser.add_argument(
            "--operator-password",
            default="",
            help="Set only when creating the operator. Prompted if omitted.",
        )

    def handle(self, *args, **options):
        host = options["host"].strip().lower().rstrip(".")
        if not host:
            raise CommandError("--host is required.")

        count = Tenant.objects.count()
        if count > 1:
            raise CommandError(
                f"This database holds {count} clients. One hostname can only serve one of "
                "them, and pointing it at one would strand the rest. Use host-based "
                "routing (CONSOLE_HOST + a wildcard domain) instead."
            )

        call_command("seed_plans", verbosity=0)
        self.stdout.write("plans seeded")

        tenant = Tenant.objects.first()
        if tenant is None:
            tenant = self._create(options, host)
        else:
            self._adopt(tenant, host)

        operator = self._operator(options)
        self._report(host, tenant, operator)

    # ------------------------------------------------------------------ steps
    def _create(self, options, host) -> Tenant:
        plan = Plan.objects.filter(slug="full").first() or Plan.objects.first()
        result = services.provision_tenant(
            slug=options["slug"],
            name=options["name"],
            plan=plan,
            # The host is set explicitly below; provisioning would otherwise
            # build "<slug>.<base domain>", which does not exist here.
            base_domain=host,
            owner_username="admin",
            owner_name=options["name"],
            trial_days=None,
            seed_academics=True,
        )
        tenant = result["tenant"]
        tenant.domains.all().delete()
        Domain.objects.create(tenant=tenant, host=host, is_primary=True)
        self.stdout.write(self.style.SUCCESS(f"created center '{tenant.slug}' at {host}"))
        self.stdout.write(f"  center admin: admin / {result['password']}")
        self.stdout.write(
            self.style.WARNING("  shown once — it is not stored anywhere. Copy it now.")
        )
        return tenant

    def _adopt(self, tenant: Tenant, host: str) -> None:
        existing = Domain.objects.filter(host=host).first()
        if existing is not None and existing.tenant_id != tenant.pk:
            raise CommandError(f"{host} already points at another client.")
        if existing is None:
            Domain.objects.create(
                tenant=tenant,
                host=host,
                is_primary=not tenant.domains.filter(is_primary=True).exists(),
            )
            self.stdout.write(self.style.SUCCESS(f"{host} -> {tenant.slug}"))
        else:
            self.stdout.write(f"{host} already points at {tenant.slug}")

        if tenant.status != TenantStatus.ACTIVE:
            self.stdout.write(
                self.style.WARNING(
                    f"note: this center is {tenant.status}; sign-in is blocked until it is "
                    "resumed from the console."
                )
            )

    def _operator(self, options) -> User:
        username = options["operator"]
        operator = User.all_tenants.filter(username=username, tenant__isnull=True).first()
        if operator is not None:
            if not operator.is_platform_staff:
                operator.is_platform_staff = True
                operator.save(update_fields=["is_platform_staff"])
            self.stdout.write(f"operator '{username}' already exists (password unchanged)")
            return operator

        password = options["operator_password"]
        if not password:
            import getpass

            password = getpass.getpass(f"Password for console operator '{username}': ")
        if len(password) < 10:
            raise CommandError("Choose a password of at least 10 characters.")

        operator = User.objects.create_user(
            username=username,
            password=password,
            is_platform_staff=True,
            full_name="Platform operator",
        )
        self.stdout.write(self.style.SUCCESS(f"operator '{username}' created"))
        return operator

    # ----------------------------------------------------------------- report
    def _report(self, host, tenant, operator):
        prefix = (getattr(settings, "CONSOLE_PATH_PREFIX", "") or "").strip("/")
        rule = "-" * 66

        self.stdout.write("")
        self.stdout.write(rule)
        self.stdout.write(f"  Center   https://{host}/accounts/login/")
        if prefix:
            self.stdout.write(f"  Console  https://{host}/{prefix}/login/  ({operator.username})")
        else:
            self.stdout.write(
                self.style.WARNING(
                    "  Console  unreachable: CONSOLE_PATH_PREFIX is empty and this host "
                    "serves the center."
                )
            )
        self.stdout.write(rule)
