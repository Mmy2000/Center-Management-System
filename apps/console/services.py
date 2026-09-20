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
from django.utils.translation import gettext as _

from apps.tenancy.constants import BillingCycle, FeatureState, PlatformAction, TenantStatus
from apps.tenancy.context import tenant_context
from apps.tenancy.features import spec_for
from apps.tenancy.models import (
    Domain,
    Plan,
    PlanFeature,
    Tenant,
    TenantFeature,
    TenantRatePolicy,
    TenantUsage,
)
from apps.tenancy.platform_audit import record
from apps.tenancy.resolver import invalidate_plan

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
    billing_cycle: str = BillingCycle.MONTHLY,
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
        billing_cycle=billing_cycle,
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
    from apps.core.http import DomainError

    spec = spec_for(feature_key)
    if spec.is_core:
        raise DomainError(
            "ERR_CORE_FEATURE",
            _("لا يمكن تعطيل خاصية أساسية."),
            status=409,
            data={"feature": feature_key},
        )

    # A center must keep at least one way of taking attendance. Refusing here
    # rather than warning: the alternative is a center whose scanner has nowhere
    # to go and whose lesson roll can never be filled, discovered by a
    # receptionist on a Saturday morning.
    if spec.is_method and state == FeatureState.OFF:
        from apps.tenancy.resolver import remaining_methods

        if not remaining_methods(tenant, without=feature_key):
            raise DomainError(
                "ERR_LAST_METHOD",
                _("لا يمكن تعطيل آخر طريقة لتسجيل الحضور. فعّل طريقة أخرى أولًا."),
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


@transaction.atomic
def save_plan(form, *, actor=None) -> Plan:
    """Create or update a plan and its feature set, in one transaction.

    The feature rows are replaced wholesale rather than diffed: a plan's grant
    list is small, and "what it grants now" is easier to reason about than a
    sequence of adds and removes.
    """
    creating = form.instance.pk is None
    # Re-read from the database: `form.instance` already carries the submitted
    # values by the time `is_valid()` has run, so snapshotting it here would
    # compare the new values against themselves and record nothing.
    before = {} if creating else _plan_snapshot(Plan.objects.get(pk=form.instance.pk))

    plan = form.save()
    wanted = set(form.cleaned_data["features"])
    current = plan.feature_keys

    PlanFeature.objects.filter(plan=plan, feature_key__in=current - wanted).delete()
    PlanFeature.objects.bulk_create(
        [PlanFeature(plan=plan, feature_key=key) for key in sorted(wanted - current)]
    )

    # Every tenant on this plan has to re-resolve: the grant list just moved.
    invalidate_plan(plan.pk)

    after = _plan_snapshot(plan)
    record(
        PlatformAction.PLAN_CREATED if creating else PlatformAction.PLAN_UPDATED,
        tenant=None,
        actor=actor,
        object_repr=plan.name,
        changes=(
            after
            if creating
            else {key: [before[key], after[key]] for key in after if before.get(key) != after[key]}
        ),
    )
    return plan


def _plan_snapshot(plan: Plan) -> dict:
    return {
        "name": plan.name,
        "is_active": plan.is_active,
        "monthly_price": str(plan.monthly_price) if plan.monthly_price is not None else None,
        "yearly_price": str(plan.yearly_price) if plan.yearly_price is not None else None,
        "discount_percent": str(plan.discount_percent),
        "max_students": plan.max_students,
        "max_users": plan.max_users,
        "max_groups": plan.max_groups,
        "max_cards": plan.max_cards,
        "features": sorted(plan.feature_keys),
    }


def set_plan_active(plan: Plan, active: bool, *, actor=None) -> Plan:
    """Retire a plan without touching the clients already on it.

    Deactivating hides it from the provisioning wizard and the plan picker; it
    does not move, downgrade or warn a single existing client. Changing what
    someone already pays for is a conversation, not a side effect of tidying up
    a price list.
    """
    if plan.is_active == active:
        return plan
    plan.is_active = active
    plan.save(update_fields=["is_active", "updated_at"])
    record(
        PlatformAction.PLAN_UPDATED,
        tenant=None,
        actor=actor,
        object_repr=plan.name,
        changes={"is_active": [not active, active]},
    )
    return plan


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
            warnings.append(_("ستفقد: %(feature)s") % {"feature": spec.label})

    for resource in ("students", "users", "groups", "cards"):
        cap = getattr(plan, f"max_{resource}", None)
        if cap is None:
            continue
        used = quota.usage(tenant, resource, live=True)
        if used > cap:
            warnings.append(
                _("العميل لديه %(used)s %(resource)s والحد في هذه الباقة %(cap)s.")
                % {"used": used, "resource": quota.LABELS[resource], "cap": cap}
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


# --------------------------------------------------------------------------- #
# Traffic control (TASK-122)
# --------------------------------------------------------------------------- #

#: The fields an operator can move. Kept as a tuple so the audit diff, the
#: validation and the write all walk the same list and cannot fall out of step.
RATE_FIELDS = ("max_rps", "max_rpm", "max_rph", "burst")


def rate_policy_for(tenant) -> TenantRatePolicy:
    """The tenant's policy row, unsaved if it does not exist yet.

    Unsaved rather than ``get_or_create``: reading a screen must not create
    rows. Absence is the default, and a row that merely restates the default
    is a row that will eventually disagree with it — the same reasoning
    ``TenantFeature`` applies to ``INHERIT``.
    """
    existing = TenantRatePolicy.objects.filter(tenant=tenant).first()
    return existing if existing is not None else TenantRatePolicy(tenant=tenant)


def _snapshot_policy(policy: TenantRatePolicy) -> dict:
    data = {name: getattr(policy, name) for name in RATE_FIELDS}
    data["blocked"] = policy.blocked
    return data


@transaction.atomic
def set_rate_limits(tenant, *, actor=None, reason: str = "", **limits) -> TenantRatePolicy:
    """Set, change or clear this client's request limits.

    ``None`` for a window means unlimited, and clearing every window on an
    unblocked client deletes the row outright: "no limits" then has exactly one
    representation, and the hot path has one less state to be wrong about.

    Nothing here touches ``Tenant.status``. Throttling a center is not a
    billing event and must not appear in their subscription history as one.
    """
    policy = rate_policy_for(tenant)
    before = _snapshot_policy(policy)

    for name in RATE_FIELDS:
        if name in limits:
            value = limits[name]
            setattr(policy, name, 0 if name == "burst" and value is None else value)

    policy.updated_by = actor
    policy.full_clean(exclude=["tenant", "updated_by"])

    after = _snapshot_policy(policy)
    changes = {name: [before[name], after[name]] for name in after if before[name] != after[name]}

    if not policy.is_default:
        policy.save()
    elif policy.pk:
        # Every limit cleared on an unblocked client: drop the row rather than
        # keep one that says exactly what its absence already says.
        policy.delete()

    if changes:
        record(
            PlatformAction.TRAFFIC_LIMITED,
            tenant=tenant,
            actor=actor,
            reason=reason,
            changes=changes,
        )
    return policy


@transaction.atomic
def block_traffic(tenant, *, reason: str, actor=None) -> TenantRatePolicy:
    """Refuse every request from this client until someone lifts it.

    A door, not a shredder — the same promise suspension makes. No data is
    touched, no session is destroyed and no configuration is lost; the centre's
    own users simply get a 429 page until the block comes off. The limits
    already on the row are left exactly as they are, so lifting the block
    restores the previous throttle rather than silently removing it.
    """
    policy = rate_policy_for(tenant)
    if policy.blocked:
        return policy

    policy.blocked = True
    policy.blocked_reason = reason
    policy.blocked_at = timezone.now()
    policy.updated_by = actor
    policy.save()

    record(PlatformAction.TRAFFIC_BLOCKED, tenant=tenant, actor=actor, reason=reason)
    return policy


@transaction.atomic
def unblock_traffic(tenant, *, reason: str = "", actor=None) -> TenantRatePolicy:
    """Let the client's traffic through again, immediately."""
    policy = rate_policy_for(tenant)
    if not policy.pk or not policy.blocked:
        return policy

    policy.blocked = False
    policy.blocked_reason = ""
    policy.blocked_at = None
    policy.updated_by = actor

    # Nothing left to say once the block is off and no limit was ever set:
    # delete rather than keep a row that means "default".
    if policy.is_default:
        policy.delete()
    else:
        policy.save()

    record(PlatformAction.TRAFFIC_UNBLOCKED, tenant=tenant, actor=actor, reason=reason)
    return policy


def random_slug_suggestion(name: str) -> str:
    """A DNS-safe first guess at a slug, for the wizard to prefill."""
    allowed = string.ascii_lowercase + string.digits + "-"
    candidate = "".join(ch if ch in allowed else "-" for ch in name.lower())
    return candidate.strip("-")[:32] or "center"
