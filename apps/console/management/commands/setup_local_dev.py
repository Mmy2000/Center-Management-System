"""Make a multi-tenant install runnable on a laptop, in one command.

A multi-tenant app is harder to start than a single-tenant one: every request
needs a hostname that resolves to a client, and the console needs a hostname of
its own. Without this, a fresh checkout answers "Unknown host" to the very URL
`runserver` just printed, and gives no hint why — which is a defect in the
product, not in the developer.

    python manage.py setup_local_dev

Idempotent. It never touches a real client's data; the only password it resets
is the demo client's, which it created itself.
"""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.accounts.models import User
from apps.console import services
from apps.tenancy.constants import TenantStatus
from apps.tenancy.context import tenant_context
from apps.tenancy.models import Domain, Plan, Tenant

#: Hostnames a local client should answer on. `127.0.0.1` is here because that
#: is what `runserver` prints, and being 404'd by the URL the tool just handed
#: you is a miserable first five minutes.
LOCAL_HOSTS = ["localhost", "127.0.0.1"]

OPERATOR_USERNAME = "platform"
OPERATOR_PASSWORD = "platform12345"  # noqa: S105 - local development only
DEMO_SLUG = "demo"
DEMO_PASSWORD = "demo12345678"  # noqa: S105 - local development only


class Command(BaseCommand):
    help = "Seed plans, local hostnames, a console operator and a demo client."

    def add_arguments(self, parser):
        parser.add_argument("--no-demo", action="store_true", help="Skip creating the demo client.")

    # ----------------------------------------------------------------- output
    def say(self, message: str, style=None) -> None:
        """Write a line this terminal can actually print.

        The default Windows console is cp1252, and every client name in this
        product is Arabic. Crashing the setup helper on a name — or on a
        box-drawing character — would be a poor introduction, so anything
        unencodable degrades to "?" instead of raising.
        """
        encoding = getattr(self.stdout, "encoding", None) or "utf-8"
        try:
            message.encode(encoding)
        except (UnicodeEncodeError, LookupError):
            message = message.encode(encoding, "replace").decode(encoding)
        self.stdout.write(style(message) if style else message)

    def heading(self, message: str) -> None:
        self.say(message, self.style.MIGRATE_HEADING)

    # ------------------------------------------------------------------ steps
    def handle(self, *args, **options):
        if not settings.DEBUG:
            self.stderr.write(
                self.style.ERROR("Refusing to run with DEBUG=False - this is a dev helper.")
            )
            return

        call_command("seed_plans", verbosity=0)
        self.say("plans seeded")

        primary = self.wire_local_hosts()
        operator, operator_is_new = self.ensure_operator()
        demo = None if options["no_demo"] else self.ensure_demo_client()

        self.report(primary, operator, operator_is_new, demo)

    def wire_local_hosts(self) -> Tenant | None:
        """Point every local hostname at the first client."""
        tenant = Tenant.objects.order_by("pk").first()
        if tenant is None:
            self.say("no existing client - the demo client below will be the first")
            return None

        for host in LOCAL_HOSTS:
            if Domain.objects.filter(host=host).exists():
                continue
            Domain.objects.create(
                tenant=tenant,
                host=host,
                is_primary=not tenant.domains.filter(is_primary=True).exists(),
            )
            self.say(f"domain {host} -> {tenant.slug}", self.style.SUCCESS)
        return tenant

    def ensure_operator(self) -> tuple[User, bool]:
        """A console account. Its password is set only on creation."""
        operator = User.all_tenants.filter(username=OPERATOR_USERNAME, tenant__isnull=True).first()
        if operator is not None:
            if not operator.is_platform_staff:
                operator.is_platform_staff = True
                operator.save(update_fields=["is_platform_staff"])
            self.say(f"operator {OPERATOR_USERNAME} already exists")
            return operator, False

        operator = User.objects.create_user(
            username=OPERATOR_USERNAME,
            password=OPERATOR_PASSWORD,
            is_platform_staff=True,
            full_name="Local operator",
        )
        self.say(f"operator {OPERATOR_USERNAME} created", self.style.SUCCESS)
        return operator, True

    def ensure_demo_client(self) -> Tenant:
        """A second client, so tenant isolation is visible rather than claimed.

        Unlike a real client, this one's admin password is reset on every run: a
        demo account nobody can sign into is worse than no demo account, and
        this command refuses to run outside DEBUG anyway.
        """
        tenant = Tenant.objects.filter(slug=DEMO_SLUG).first()
        created = tenant is None

        if created:
            plan = Plan.objects.filter(slug="full").first() or Plan.objects.first()
            tenant = services.provision_tenant(
                slug=DEMO_SLUG,
                name="Demo Center",
                plan=plan,
                base_domain=settings.TENANT_BASE_DOMAIN,
                owner_username="admin",
                owner_name="Demo admin",
                trial_days=30,
                seed_academics=True,
                demo=True,
            )["tenant"]

        with tenant_context(tenant):
            owner = User.objects.filter(username="admin").first()
            if owner is not None:
                owner.set_password(DEMO_PASSWORD)
                owner.must_change_password = False
                owner.save(update_fields=["password", "must_change_password"])

        verb = "created" if created else "already exists (admin password reset)"
        self.say(f"client {DEMO_SLUG} {verb}", self.style.SUCCESS if created else None)
        return tenant

    # ----------------------------------------------------------------- report
    def report(self, primary, operator, operator_is_new, demo):
        console_host = settings.CONSOLE_HOST or "(CONSOLE_HOST not set)"
        rule = "-" * 66

        self.say("")
        self.say(rule)
        self.heading("  Ready. Start the server:   python manage.py runserver")
        self.say(rule)

        self.heading("\n  PLATFORM CONSOLE  (manage every client from here)")
        self.say(f"      http://{console_host}:8000/login/")
        self.say(f"      username:  {OPERATOR_USERNAME}")
        if operator_is_new:
            self.say(f"      password:  {OPERATOR_PASSWORD}")
        else:
            self.say("      password:  unchanged - this account already existed")

        if primary is not None:
            self.heading(f"\n  CLIENT 1  ({primary.slug})")
            for host in LOCAL_HOSTS:
                if Domain.objects.filter(host=host, tenant=primary).exists():
                    self.say(f"      http://{host}:8000/accounts/login/")
            names = list(
                User.all_tenants.filter(tenant=primary).values_list("username", flat=True)[:6]
            )
            self.say(f"      users:     {', '.join(names) or '(none)'}")
            self.say("      password:  whatever you already set for them")

        if demo is not None:
            self.heading(f"\n  CLIENT 2  ({demo.slug})  - built by this command")
            self.say(f"      http://{demo.primary_host}:8000/accounts/login/")
            self.say("      username:  admin")
            self.say(f"      password:  {DEMO_PASSWORD}")

        self.heading("\n  TRY THIS - it is the whole point of the branch")
        self.say("      1. Sign in to the console, open a client, click Features.")
        self.say("      2. Set 'payments' to OFF. Reload that client's site:")
        self.say("         the Payments menu is gone and /payments/ returns 404.")
        self.say("      3. Check the OTHER client - still has Payments. Nothing shared.")
        self.say("      4. Set it back to INHERIT: every charge and receipt is still there.")
        self.say("      5. Suspend the client, reload their site: a gate page, no data lost.")
        self.say(rule)

        if ".localhost" in str(settings.CONSOLE_HOST):
            self.say("")
            self.say("  Browsers resolve *.localhost to 127.0.0.1 by themselves. If yours")
            self.say("  does not, add one line to C:\\Windows\\System32\\drivers\\etc\\hosts:")
            self.say(f"      127.0.0.1  {settings.CONSOLE_HOST} {DEMO_SLUG}.localhost")

        stuck = Tenant.objects.filter(
            status__in=[TenantStatus.SUSPENDED, TenantStatus.ARCHIVED]
        ).count()
        if stuck:
            self.say(f"\n  Note: {stuck} client(s) are suspended or archived.", self.style.WARNING)
