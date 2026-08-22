"""Two centers, identical data, nothing crosses (docs/10 §N.15, TASK-119).

The method is deliberate: both tenants get the *same* student codes, the same
card numbers, the same receipt numbers, the same usernames and the same subject
codes. Under those conditions a leak is unambiguous — a count of 2 where there
should be 1, or a row fetched by a primary key that belongs to someone else.
Identical data also proves the per-tenant unique constraints are doing their
job, because none of it could coexist under the old global ones.

The model sweep is parametrised over the model registry rather than a hand-kept
list, so a model added later is covered the day it is added.
"""

from datetime import time, timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.tenancy.base import TenantOwnedModel
from apps.tenancy.context import tenant_context
from apps.tenancy.exceptions import CrossTenantWrite, TenantContextRequired

from .factories import make_two_tenants

pytestmark = pytest.mark.django_db

# Identical in both centers on purpose — see the module docstring.
STAGE_CODE = "SEC"
SUBJECT_CODE = "PHY"
GROUP_CODE = "SEC3-PHY-A"
STUDENT_CODE = "S000001"
CARD_NUMBER = "C000001"
RECEIPT_NUMBER = "R000001"
USERNAME = "admin"


def build_center(tenant, *, token_suffix):
    """One complete center: stage → group → student → card → lesson → money.

    ``token_suffix`` is the single value that differs, because ``qr_token`` is
    globally unique by design (docs/10 §N.5) — a 64-char random token costs
    nothing to keep unique across the whole platform and guarantees a scan can
    never resolve to two rows.
    """
    from django.db import transaction

    from apps.academics.models import (
        EducationalStage,
        Grade,
        GradeSubject,
        Group,
        Instructor,
        Subject,
    )
    from apps.accounts.models import User
    from apps.attendance.models import Attendance, AttendanceEvent
    from apps.cards.models import CardStatus, StudentCard
    from apps.core.registry import settings_registry
    from apps.core.sequences import next_number
    from apps.lessons.models import Lesson
    from apps.payments.models import MonthlyCharge, Payment
    from apps.students.models import Student

    with tenant_context(tenant):
        # A policy override and a counter, so the sweep below covers core.Setting
        # and core.Sequence with real rows instead of skipping them.
        settings_registry.set("center.name", f"مركز {tenant.slug}")
        with transaction.atomic():
            next_number("student_code")

        user = User.objects.create_user(username=USERNAME, password="TestPass!2026")
        stage = EducationalStage.objects.create(name="Secondary", code=STAGE_CODE, order=3)
        grade = Grade.objects.create(stage=stage, name="G3", code="SEC3", order=3)
        subject = Subject.objects.create(name="Physics", code=SUBJECT_CODE)
        offering = GradeSubject.objects.create(
            grade=grade, subject=subject, default_monthly_fee=Decimal("500.00")
        )
        instructor = Instructor.objects.create(full_name="مدرّس", phone="01000000000")
        group = Group.objects.create(
            grade_subject=offering,
            name="Group A",
            code=GROUP_CODE,
            instructor=instructor,
            monthly_fee=Decimal("500.00"),
        )
        group.schedules.create(weekday=0, start_time=time(16, 0), end_time=time(18, 0))
        student = Student.objects.create(student_code=STUDENT_CODE, full_name="طالب", grade=grade)
        student.assignments.create(group=group, grade_subject=offering)
        card = StudentCard.objects.create(
            card_number=CARD_NUMBER,
            qr_token=f"token-{token_suffix}",
            status=CardStatus.ASSIGNED,
            current_student=student,
        )
        card.assignment_history.create(student=student, assigned_at=timezone.now())

        start = timezone.now()
        lesson = Lesson.objects.create(
            group=group,
            scheduled_start=start,
            scheduled_end=start + timedelta(hours=2),
            check_in_opens_at=start - timedelta(minutes=30),
            check_in_closes_at=start + timedelta(minutes=90),
            late_after=start + timedelta(minutes=15),
        )
        AttendanceEvent.objects.create(
            lesson=lesson,
            student=student,
            card=card,
            event_type="CHECK_IN",
            result_code="OK",
            idempotency_key="same-key-in-both-centers",
        )
        Attendance.objects.create(
            lesson=lesson,
            student=student,
            assigned_group=group,
            attended_group=group,
            state="CHECKED_IN",
            status="PRESENT",
            check_in_at=start,
        )
        charge = MonthlyCharge.objects.create(
            student=student,
            grade_subject=offering,
            group=group,
            billing_month=timezone.localdate().replace(day=1),
            amount_due=Decimal("500.00"),
        )
        Payment.objects.create(
            monthly_charge=charge,
            student=student,
            amount=Decimal("100.00"),
            paid_at=timezone.now(),
            receipt_number=RECEIPT_NUMBER,
            collected_by=user,
        )
        return {
            "user": user,
            "stage": stage,
            "group": group,
            "student": student,
            "card": card,
            "lesson": lesson,
            "charge": charge,
        }


@pytest.fixture
def two_centers():
    alpha, beta = make_two_tenants()
    return (alpha, build_center(alpha, token_suffix="a")), (
        beta,
        build_center(beta, token_suffix="b"),
    )


def tenant_owned_models():
    return sorted(
        (m for m in django_apps.get_models() if issubclass(m, TenantOwnedModel)),
        key=lambda m: m._meta.label_lower,
    )


def test_the_sweep_actually_covers_the_domain():
    """Guard the guard: if this list silently emptied, every test below would
    pass while proving nothing."""
    labels = {m._meta.label_lower for m in tenant_owned_models()}
    assert len(labels) >= 18
    for expected in (
        "students.student",
        "cards.studentcard",
        "payments.payment",
        "attendance.attendance",
        "core.setting",
        "core.sequence",
    ):
        assert expected in labels


# --------------------------------------------------------------------------- #
# The model sweep
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", tenant_owned_models(), ids=lambda m: m._meta.label_lower)
def test_each_center_sees_only_its_own_rows(model, two_centers):
    (alpha, _), (beta, _) = two_centers
    total = model.all_tenants.count()
    with tenant_context(alpha):
        mine = model.objects.count()
    with tenant_context(beta):
        theirs = model.objects.count()
    assert mine + theirs == total
    if total:
        assert mine and theirs, f"{model._meta.label_lower}: fixture built nothing to compare"


@pytest.mark.parametrize("model", tenant_owned_models(), ids=lambda m: m._meta.label_lower)
def test_another_centers_row_is_invisible_by_primary_key(model, two_centers):
    """The sharpest form of the leak: asking for a key you already know."""
    (alpha, _), (beta, _) = two_centers
    with tenant_context(beta):
        theirs = model.objects.first()
    if theirs is None:
        pytest.skip(f"{model._meta.label_lower}: nothing in the second center to ask for")
    with tenant_context(alpha):
        assert not model.objects.filter(pk=theirs.pk).exists()
        with pytest.raises(model.DoesNotExist):
            model.objects.get(pk=theirs.pk)


@pytest.mark.parametrize("model", tenant_owned_models(), ids=lambda m: m._meta.label_lower)
def test_no_query_runs_without_a_tenant(model):
    with pytest.raises(TenantContextRequired):
        list(model.objects.all())


@pytest.mark.parametrize("model", tenant_owned_models(), ids=lambda m: m._meta.label_lower)
def test_update_stays_inside_the_tenant(model, two_centers):
    """`.update()` never reaches `save()`, so the manager's filter is the only
    thing in front of it. ``updated_at`` is the one column every tenant-owned
    model has, which makes it the probe that works for all of them."""
    (alpha, _), (beta, _) = two_centers
    stamp = timezone.now() - timedelta(days=365)
    with tenant_context(alpha):
        model.objects.update(updated_at=stamp)
    for row in model.all_tenants.filter(tenant=beta):
        assert row.updated_at != stamp, f"{model._meta.label_lower} updated across tenants"
    assert (
        model.all_tenants.filter(tenant=alpha, updated_at=stamp).count()
        == model.all_tenants.filter(tenant=alpha).count()
    )


@pytest.mark.parametrize("model", tenant_owned_models(), ids=lambda m: m._meta.label_lower)
def test_delete_stays_inside_the_tenant(model, two_centers):
    """Deleting everything in one center must leave the other untouched.

    Most of these rows are protected by foreign keys, so the delete raises
    instead of running — which is itself the correct outcome. Either way the
    other center's rows must still be there afterwards.
    """
    from django.db.models import ProtectedError

    (alpha, _), (beta, _) = two_centers
    before = model.all_tenants.filter(tenant=beta).count()
    with tenant_context(alpha):
        try:
            model.objects.all().delete()
        except ProtectedError:
            pass
    assert model.all_tenants.filter(tenant=beta).count() == before


# --------------------------------------------------------------------------- #
# The scenarios that matter most
# --------------------------------------------------------------------------- #


def test_identical_data_can_coexist(two_centers):
    """None of this could exist at once under the old global unique columns."""
    from apps.academics.models import Subject
    from apps.cards.models import StudentCard
    from apps.payments.models import Payment
    from apps.students.models import Student

    assert Student.all_tenants.filter(student_code=STUDENT_CODE).count() == 2
    assert StudentCard.all_tenants.filter(card_number=CARD_NUMBER).count() == 2
    assert Payment.all_tenants.filter(receipt_number=RECEIPT_NUMBER).count() == 2
    assert Subject.all_tenants.filter(code=SUBJECT_CODE).count() == 2


def test_the_same_username_in_both_centers(two_centers):
    from apps.accounts.models import User

    assert User.all_tenants.filter(username=USERNAME).count() == 2


def test_logging_in_reaches_only_the_matching_center(two_centers):
    """A username that exists twice must resolve by host, not by luck."""
    from django.contrib.auth import authenticate

    (alpha, alpha_data), (beta, beta_data) = two_centers
    with tenant_context(alpha):
        user = authenticate(username=USERNAME, password="TestPass!2026")
    assert user is not None
    assert user.pk == alpha_data["user"].pk
    assert user.pk != beta_data["user"].pk


def test_a_suspended_center_cannot_be_authenticated_into(two_centers):
    from django.contrib.auth import authenticate

    from apps.tenancy.constants import TenantStatus

    (alpha, _), _ = two_centers
    alpha.status = TenantStatus.SUSPENDED
    alpha.save()
    with tenant_context(alpha):
        assert authenticate(username=USERNAME, password="TestPass!2026") is None


def test_scanning_another_centers_token_finds_nothing(two_centers):
    """Cross-tenant scan: an unknown card, not a hit and not a crash."""
    from apps.cards.models import StudentCard

    (alpha, _), (beta, beta_data) = two_centers
    with tenant_context(alpha):
        assert not StudentCard.objects.filter(qr_token=beta_data["card"].qr_token).exists()


def test_qr_tokens_stay_globally_unique(two_centers):
    """Deliberate (docs/10 §N.5): a token must never resolve to two rows."""
    from django.db import IntegrityError, transaction

    from apps.cards.models import CardStatus, StudentCard

    (alpha, alpha_data), (beta, beta_data) = two_centers
    with tenant_context(alpha), pytest.raises(IntegrityError), transaction.atomic():
        StudentCard.objects.create(
            card_number="C000002",
            qr_token=beta_data["card"].qr_token,
            status=CardStatus.AVAILABLE,
        )


def test_settings_are_isolated(two_centers):
    from apps.core.registry import settings_registry

    (alpha, _), (beta, _) = two_centers
    with tenant_context(alpha):
        settings_registry.set("attendance.late_after_minutes", 45)
        assert settings_registry.get("attendance.late_after_minutes") == 45
    with tenant_context(beta):
        # Same process, same cache backend — the key has to be namespaced or
        # this reads alpha's value, and no queryset filter would catch it.
        assert settings_registry.get("attendance.late_after_minutes") == 15


def test_sequences_restart_in_each_center(two_centers):
    from django.db import transaction

    from apps.core.sequences import next_number

    (alpha, _), (beta, _) = two_centers
    with tenant_context(alpha), transaction.atomic():
        assert next_number("invoice") == 1
        assert next_number("invoice") == 2
    with tenant_context(beta), transaction.atomic():
        assert next_number("invoice") == 1


def test_rate_limit_budgets_are_separate(two_centers):
    from apps.core import ratelimit

    (alpha, _), (beta, _) = two_centers
    with tenant_context(alpha):
        for _ in range(3):
            ratelimit.hit("login", "1.2.3.4", limit=3, window=60)
        assert ratelimit.hit("login", "1.2.3.4", limit=3, window=60) is False
    with tenant_context(beta):
        # One center's traffic must not spend another's budget.
        assert ratelimit.hit("login", "1.2.3.4", limit=3, window=60) is True


def test_audit_rows_are_stamped_and_scoped(two_centers):
    from apps.core.audit import record
    from apps.core.models import AuditLog

    (alpha, alpha_data), (beta, _) = two_centers
    with tenant_context(alpha):
        entry = record("STUDENT_UPDATED", alpha_data["student"])
    assert entry.tenant_id == alpha.pk
    assert not AuditLog.objects.filter(tenant=beta, pk=entry.pk).exists()


def test_a_platform_action_records_no_tenant(two_centers):
    from apps.core.audit import record

    entry = record("SETTING_CHANGED", None, object_repr="platform")
    assert entry.tenant_id is None


def test_a_foreign_key_cannot_point_across_centers(two_centers):
    """The database cannot express this without composite FKs, so the model does."""
    from apps.lessons.models import Lesson

    (alpha, _), (beta, beta_data) = two_centers
    start = timezone.now()
    with tenant_context(alpha), pytest.raises(CrossTenantWrite):
        Lesson.objects.create(
            group=beta_data["group"],
            scheduled_start=start,
            scheduled_end=start + timedelta(hours=2),
            check_in_opens_at=start,
            check_in_closes_at=start + timedelta(hours=2),
            late_after=start,
        )


def test_a_subquery_is_scoped_too(two_centers):
    """A deferred queryset used as `__in=` must resolve before the SQL is built,
    or it slips past the filter entirely."""
    from apps.academics.models import Group
    from apps.students.models import Student

    (alpha, _), (beta, _) = two_centers
    with tenant_context(alpha):
        groups = Group.objects.all()
        found = Student.objects.filter(assignments__group__in=groups).distinct()
        assert {s.tenant_id for s in found} == {alpha.pk}


def test_all_tenants_is_confined_to_tenancy_and_console():
    """The escape hatch must stay greppable — that is the whole point of it.

    Parsed rather than grepped: only a real attribute *access*
    (``Student.all_tenants``) counts. The manager declarations on the models and
    the mentions in docstrings are not uses, and flagging them would make the
    test cry wolf until somebody deleted it.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    allowed = ("apps/tenancy/", "apps/console/", "/migrations/")
    offenders = []
    for path in (root / "apps").rglob("*.py"):
        posix = path.as_posix()
        if any(fragment in posix for fragment in allowed):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "all_tenants":
                offenders.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    assert offenders == [], (
        "all_tenants is the deliberate, audited way to cross a tenant boundary. "
        f"It must not be used outside tenancy/console/migrations: {offenders}"
    )
