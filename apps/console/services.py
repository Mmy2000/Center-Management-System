"""Everything the console does *to* a client (docs/10 §N.10, TASK-113/114/116).

The console's views are thin: they validate a form and call one of these. That
keeps the management commands and the web UI on exactly the same code path, so
``provision_tenant --slug elnour`` and the wizard cannot drift apart — which is
what makes the wizard safe to trust for something as consequential as creating
a client.
"""

import secrets
import string

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.tenancy.constants import FeatureState, PlatformAction, TenantStatus
from apps.tenancy.context import tenant_context
from apps.tenancy.features import spec_for
from apps.tenancy.models import Domain, Plan, Tenant, TenantFeature, TenantUsage
from apps.tenancy.platform_audit import record

#: Unambiguous alphabet — no O/0, l/1/I. A one-time password gets read aloud
#: down a phone line at least once.
PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def make_password(length: int = 14) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def host_for(slug: str, base_domain: str) -> str:
    return f"{slug}.{base_domain}".strip(".").lower()


@transaction.atomic
def provision_tenant(
    *,
    slug: str,
    name: str,
    plan: Plan,
    base_domain: str,
    owner_username: str = "admin",
    owner_name: str = "",
    owner_email: str = "",
    owner_phone: str = "",
    trial_days: int | None = 30,
    seed_academics: bool = False,
    demo: bool = False,
    actor=None,
) -> dict:
    """Create a client, ready to log into. One transaction, or nothing.

    A failure at any step leaves no half-built tenant — which matters because a
    half-built one is worse than none: it holds the slug, answers on the
    subdomain, and has no way in.
    """
    from django.core.management import call_command

    from apps.accounts.models import Role, User
    from apps.accounts.services import sync_user_group

    tenant = Tenant(
        slug=slug,
        name=name,
        plan=plan,
        status=TenantStatus.TRIAL if trial_days else TenantStatus.ACTIVE,
        owner_name=owner_name,
        owner_email=owner_email,
        owner_phone=owner_phone,
    )
    if trial_days:
        tenant.trial_ends_at = timezone.now() + timezone.timedelta(days=trial_days)
    else:
        tenant.activated_at = timezone.now()
    tenant.full_clean(exclude=["plan"])
    tenant.save()

    Domain.objects.create(
        tenant=tenant, host=host_for(slug, base_domain), is_primary=True, is_custom=False
    )
    TenantUsage.objects.create(tenant=tenant)

    password = make_password()
    with tenant_context(tenant):
        # Roles are global (five auth.Groups shared by every center — docs/10
        # §N.6); this is a no-op after the first tenant and costs one query.
        call_command("seed_roles", verbosity=0)
        # Policies need no seeding: core.Setting holds *overrides only* and the
        # defaults live in core/policies.py, so a brand-new center already has
        # every policy at its documented default with zero rows.
        if seed_academics:
            call_command("seed_academics", *(["--demo"] if demo else []), verbosity=0)

        owner = User.objects.create_user(
            username=owner_username,
            password=password,
            role=Role.CENTER_ADMIN,
            full_name=owner_name or name,
            phone=owner_phone,
            must_change_password=True,
        )
        sync_user_group(owner)

    record(
        PlatformAction.TENANT_CREATED,
        tenant=tenant,
        actor=actor,
        changes={"slug": slug, "plan": plan.slug, "host": tenant.primary_host},
    )
    # The password is returned, never stored and never logged: it is shown on
    # screen exactly once and the owner must change it at first login.
    return {"tenant": tenant, "owner": owner, "password": password}


def set_feature(tenant, feature_key: str, state: str, *, actor=None, note: str = ""):
    """Move one feature's per-client override.

    ``INHERIT`` **deletes** the row rather than storing the word. Keeping an
    "inherit" row would let the plan default and the stored state drift apart,
    and then nobody could tell which one was answering.
    """
    spec = spec_for(feature_key)
    if spec.is_core:
        from apps.core.http import DomainError

        raise DomainError(
            "ERR_CORE_FEATURE",
            "لا يمكن تعطيل خاصية أساسية.",
            status=409,
            data={"feature": feature_key},
        )

    previous = (
        TenantFeature.objects.filter(tenant=tenant, feature_key=feature_key)
        .values_list("state", flat=True)
        .first()
        or FeatureState.INHERIT
    )

    if state == FeatureState.INHERIT:
        TenantFeature.objects.filter(tenant=tenant, feature_key=feature_key).delete()
    else:
        TenantFeature.objects.update_or_create(
            tenant=tenant,
            feature_key=feature_key,
            defaults={"state": state, "note": note, "updated_by": actor},
        )

    if previous != state:
        record(
            PlatformAction.FEATURE_CHANGED,
            tenant=tenant,
            actor=actor,
            object_repr=feature_key,
            changes={feature_key: [previous, state]},
            reason=note,
        )
    return state


def change_plan(tenant, plan: Plan, *, actor=None, reason: str = ""):
    previous = tenant.plan
    if previous.pk == plan.pk:
        return tenant
    tenant.plan = plan
    tenant.save(update_fields=["plan", "updated_at"])
    record(
        PlatformAction.PLAN_CHANGED,
        tenant=tenant,
        actor=actor,
        reason=reason,
        changes={"plan": [previous.slug, plan.slug]},
    )
    return tenant


def plan_change_warnings(tenant, plan: Plan) -> list[str]:
    """What moving to ``plan`` would take away, or already exceeds.

    Shown before the change, not after: an operator should not discover that a
    client is 300 students over their new cap by having the client phone them.
    """
    from apps.tenancy import quota
    from apps.tenancy.resolver import enabled_features

    warnings = []

    current = enabled_features(tenant)
    losing = sorted(current - plan.feature_keys)
    for key in losing:
        spec = spec_for(key)
        if not spec.is_core:
            warnings.append(f"ستفقد: {spec.label}")

    for resource in ("students", "users", "groups", "cards"):
        cap = getattr(plan, f"max_{resource}", None)
        if cap is None:
            continue
        used = quota.usage(tenant, resource, live=True)
        if used > cap:
            warnings.append(
                f"العميل لديه {used} {quota.LABELS[resource]} والحد في هذه الباقة {cap}."
            )
    return warnings


def refresh_usage(tenant) -> TenantUsage:
    """Recount everything the console shows for one client."""
    from apps.tenancy import quota

    row, _created = TenantUsage.objects.get_or_create(tenant=tenant)
    for resource in quota.RESOURCES:
        setattr(row, quota.RESOURCES[resource][2], quota._live_count(tenant, resource))

    with tenant_context(tenant):
        from datetime import timedelta

        from apps.attendance.models import AttendanceEvent
        from apps.lessons.models import Lesson
        from apps.payments.models import Payment
        from apps.students.models import Student, StudentStatus

        since = timezone.now() - timedelta(days=30)
        row.active_students = Student.objects.filter(status=StudentStatus.ACTIVE).count()
        row.lessons_30d = Lesson.objects.filter(scheduled_start__gte=since).count()
        row.scans_30d = AttendanceEvent.objects.filter(created_at__gte=since).count()
        row.payments_30d_total = (
            Payment.objects.filter(paid_at__gte=since).aggregate(total=Sum("amount"))["total"] or 0
        )

    row.computed_at = timezone.now()
    row.save()
    return row


def suspend(tenant, *, reason: str, actor=None):
    return tenant.transition_to(TenantStatus.SUSPENDED, actor=actor, reason=reason)


def resume(tenant, *, reason: str = "", actor=None):
    target = TenantStatus.ACTIVE if tenant.activated_at else TenantStatus.TRIAL
    return tenant.transition_to(target, actor=actor, reason=reason)


def archive(tenant, *, reason: str, actor=None):
    return tenant.transition_to(TenantStatus.ARCHIVED, actor=actor, reason=reason)


def random_slug_suggestion(name: str) -> str:
    """A DNS-safe first guess at a slug, for the wizard to prefill."""
    allowed = string.ascii_lowercase + string.digits + "-"
    candidate = "".join(ch if ch in allowed else "-" for ch in name.lower())
    return candidate.strip("-")[:32] or "center"
