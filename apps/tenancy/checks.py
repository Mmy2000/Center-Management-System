"""``manage.py check`` rules that keep a new model from leaking (TASK-093).

The leak suite (TASK-119) proves isolation for the models that exist today. This
check is what covers the model somebody adds next month: a concrete domain model
is either tenant-owned or on an explicit, reasoned allowlist. There is no third
state, and "I forgot" is not one of them.
"""

from django.apps import apps as django_apps
from django.core.checks import Error, Tags, Warning, register

from .base import TenantOwnedModel

#: Apps whose models are checked. Third-party and django.contrib models are not
#: ours to classify.
CHECKED_APP_LABELS = frozenset(
    {
        "core",
        "accounts",
        "academics",
        "students",
        "cards",
        "lessons",
        "attendance",
        "payments",
        "reports",
        "dashboard",
    }
)

#: Models that are deliberately **not** tenant-owned, each with the reason.
#: Adding to this set is a decision that belongs in code review.
EXEMPT_MODELS = {
    # The console must read audit rows across every tenant, and platform
    # actions have no tenant at all — so this carries a nullable tenant column
    # instead of the non-null one TenantOwnedModel imposes (TASK-102).
    "core.auditlog": "nullable tenant: platform actions belong to no center",
    # A user belongs to one tenant, or to none when they are platform staff.
    # AbstractUser also brings its own manager, which TenantManager must not
    # replace or `createsuperuser` stops working (TASK-094).
    "accounts.user": "nullable tenant: platform staff sit outside every center",
    # Permission holder, managed=False, never instantiated.
    "reports.reportpermission": "unmanaged permission holder, no rows",
}

#: Models still waiting for their tenant column, mapped to the task that adds
#: it. This is the Phase 14 checklist, kept in code rather than in a document so
#: it cannot drift: each task deletes its own line, and when the dict is empty
#: the warning disappears on its own.
#:
#: A model listed here reports ``tenancy.W001`` (a warning — the retrofit is
#: known to be in progress). A model listed *nowhere* reports ``tenancy.E001``,
#: which is the case that matters: somebody added a model and forgot.
PENDING_MIGRATION = {
    "core.setting": "TASK-095",
    "core.sequence": "TASK-095",
    "academics.educationalstage": "TASK-096",
    "academics.grade": "TASK-096",
    "academics.subject": "TASK-096",
    "academics.gradesubject": "TASK-096",
    "academics.instructor": "TASK-096",
    "academics.group": "TASK-096",
    "academics.groupschedule": "TASK-096",
    "students.student": "TASK-097",
    "students.studentgroupassignment": "TASK-097",
    "cards.studentcard": "TASK-098",
    "cards.cardassignment": "TASK-098",
    "lessons.lesson": "TASK-099",
    "attendance.attendance": "TASK-100",
    "attendance.attendanceevent": "TASK-100",
    "payments.monthlycharge": "TASK-101",
    "payments.payment": "TASK-101",
}


@register(Tags.models)
def check_models_are_tenant_scoped(app_configs=None, **kwargs):
    """``tenancy.E001`` — a domain model that is neither owned nor exempt."""
    issues = []
    for model in django_apps.get_models():
        if model._meta.app_label not in CHECKED_APP_LABELS:
            continue
        if model._meta.proxy or model._meta.abstract:
            continue
        label = model._meta.label_lower

        if issubclass(model, TenantOwnedModel):
            stale = [
                name
                for name, registry in (
                    ("EXEMPT_MODELS", EXEMPT_MODELS),
                    ("PENDING_MIGRATION", PENDING_MIGRATION),
                )
                if label in registry
            ]
            for name in stale:
                issues.append(
                    Error(
                        f"{label} subclasses TenantOwnedModel but is still listed in {name}.",
                        hint=f"Remove it from tenancy.checks.{name}.",
                        obj=model,
                        id="tenancy.E002",
                    )
                )
            continue

        if label in EXEMPT_MODELS:
            continue

        if label in PENDING_MIGRATION:
            issues.append(
                Warning(
                    f"{label} is not tenant-scoped yet ({PENDING_MIGRATION[label]}).",
                    hint="Phase 14 retrofit in progress; this is expected until that task lands.",
                    obj=model,
                    id="tenancy.W001",
                )
            )
            continue

        issues.append(
            Error(
                f"{label} is a domain model but is not tenant-scoped.",
                hint=(
                    "Subclass apps.tenancy.base.TenantOwnedModel, or — if the model "
                    "genuinely belongs to no single center — add it to "
                    "tenancy.checks.EXEMPT_MODELS with the reason why."
                ),
                obj=model,
                id="tenancy.E001",
            )
        )
    return issues


@register(Tags.models)
def check_feature_catalogue(app_configs=None, **kwargs):
    """``tenancy.E003`` — a ``depends_on`` key that does not exist, or a cycle."""
    from .features import FEATURE_KEYS, FEATURES, dependency_closure

    errors = []
    for spec in FEATURES:
        for dependency in spec.depends_on:
            if dependency not in FEATURE_KEYS:
                errors.append(
                    Error(
                        f"Feature {spec.key!r} depends on unknown feature {dependency!r}.",
                        id="tenancy.E003",
                    )
                )
    if errors:
        return errors

    for spec in FEATURES:
        if spec.key in dependency_closure(spec.key):
            errors.append(
                Error(
                    f"Feature {spec.key!r} is part of a dependency cycle.",
                    hint="depends_on must form a DAG; the resolver walks it transitively.",
                    id="tenancy.E004",
                )
            )
    return errors
