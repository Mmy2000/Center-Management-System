"""Create/refresh the five role groups and their permissions (TASK-007).

Idempotent: running it twice changes nothing. Permissions belonging to apps
that have not shipped yet are listed as missing; pass ``--strict`` (used by CI
once every app exists) to turn that into a failure.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import Role, User
from apps.accounts.permissions import GROUP_NAMES, ROLE_PERMISSIONS


class Command(BaseCommand):
    help = "Seed role groups and permissions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Fail if any declared permission does not exist.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        verbosity = options.get("verbosity", 1)
        all_perms = {
            f"{p.content_type.app_label}.{p.codename}": p
            for p in Permission.objects.select_related("content_type")
        }
        missing: list[str] = []

        for role in Role:
            group, _created = Group.objects.get_or_create(name=GROUP_NAMES[role])
            declared = ROLE_PERMISSIONS[role]

            if declared == ["*"]:
                perms = list(all_perms.values())
            else:
                perms = []
                for label in declared:
                    perm = all_perms.get(label)
                    if perm is None:
                        missing.append(f"{role.value}: {label}")
                    else:
                        perms.append(perm)

            group.permissions.set(perms)
            if verbosity >= 1:
                self.stdout.write(f"{group.name}: {len(perms)} permissions")

        # Keep existing users in sync with their role.
        for user in User.objects.all():
            self._sync_user(user)

        if missing:
            if verbosity >= 1:
                self.stdout.write(
                    self.style.WARNING(
                        f"{len(missing)} declared permission(s) do not exist yet "
                        "(their app has not shipped):"
                    )
                )
                for label in missing:
                    self.stdout.write(f"  - {label}")
            if options["strict"]:
                raise CommandError("Missing permissions with --strict.")

        if verbosity >= 1:
            self.stdout.write(self.style.SUCCESS("Roles seeded."))

    @staticmethod
    def _sync_user(user):
        from apps.accounts.services import sync_user_group

        sync_user_group(user)
