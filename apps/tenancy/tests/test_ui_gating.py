"""No screen may offer a button for a feature the center does not have.

Reported from use: with `cards` switched off, the student page still showed a
"البطاقات" tab and a "ربط بطاقة" button. Both led to endpoints that correctly
answered 404 — so nothing leaked — but a button that fails when pressed is a
bug in its own right, and "I don't know if it happens anywhere else" is exactly
the question a hand-written check cannot answer.

So this does not check the places somebody remembered. It walks **every page a
center has**, with each feature off in turn, and fails if the rendered HTML
still contains a link or a fetch for that feature. New pages are covered the
day they are added to PAGES; a new feature the day it is added to GATED.
"""

import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.console import services
from apps.tenancy.constants import FeatureState
from apps.tenancy.context import tenant_context
from apps.tenancy.tests.factories import make_plan, make_tenant

pytestmark = pytest.mark.django_db

PASSWORD = "TestPass!2026"
HOST = "alpha.testserver"

#: feature -> fragments that must not appear in any page's HTML while it is off.
#: URL paths and API endpoints, because those are what a button actually does.
GATED = {
    "cards": ["/cards/", "/api/cards/"],
    "cards.bulk_import": ["/api/cards/import/", "/api/cards/generate/", "/api/cards/export/"],
    "payments": ["/payments/", "/api/payments/", "/api/charges/"],
    "attendance.qr": ["/scan/"],
    "settings.editor": ["/settings/", "/api/settings/"],
    "audit.viewer": ["/audit/"],
    "users.management": ["/accounts/users/", "/api/users/"],
    "i18n.english": ["/i18n/setlang/"],
}

#: Every page a signed-in center admin can open. A page missing from this list
#: is a page nobody is checking.
PAGES = [
    "dashboard:home",
    "students:list",
    "students:create",
    "academics:groups",
    "academics:structure",
    "lessons:list",
    "reports:index",
    "cards:list",
    "payments:workspace",
    "core:settings",
    "core:audit_log",
    "accounts:users",
    "accounts:profile",
    "attendance:scanner_picker",
]


@pytest.fixture
def center():
    """A center with one student, so the detail and edit pages render."""
    from django.core.management import call_command

    from apps.academics.models import EducationalStage, Grade
    from apps.accounts.services import sync_user_group
    from apps.students.services import create_student

    tenant = make_tenant("alpha", plan=make_plan("full"), host=HOST)
    with tenant_context(tenant):
        call_command("seed_roles", verbosity=0)
        user = User.objects.create_user(username="boss", password=PASSWORD, role=Role.SUPER_ADMIN)
        sync_user_group(user)

        stage = EducationalStage.objects.create(name="S", code="S", order=1)
        grade = Grade.objects.create(stage=stage, name="G", code="G", order=1)
        student = create_student(full_name="طالب", grade=grade)

    client = Client()
    client.defaults["HTTP_HOST"] = HOST
    with tenant_context(tenant):
        assert client.login(username="boss", password=PASSWORD)
    return tenant, client, student


def visit(client, student):
    """Every page, as (label, html) for the ones that render."""
    pages = [(name, reverse(name)) for name in PAGES]
    pages.append(("students:detail", reverse("students:detail", args=[student.pk])))
    pages.append(("students:edit", reverse("students:edit", args=[student.pk])))

    seen = []
    for label, url in pages:
        response = client.get(url)
        # 404 is the correct answer for a page the feature gate removed; the
        # point of this sweep is the pages that *do* render.
        if response.status_code == 200:
            seen.append((label, response.content.decode("utf-8")))
    return seen


# --------------------------------------------------------------------------- #


def test_the_page_list_is_not_silently_empty(center):
    """Guard the guard: if these stopped rendering, every test below would pass
    while checking nothing."""
    tenant, client, student = center
    rendered = visit(client, student)
    assert len(rendered) >= 12, [label for label, _ in rendered]


@pytest.mark.parametrize("feature", sorted(GATED))
def test_no_page_offers_a_disabled_feature(feature, center):
    tenant, client, student = center
    services.set_feature(tenant, feature, FeatureState.OFF)

    offenders = []
    for label, html in visit(client, student):
        for fragment in GATED[feature]:
            if fragment in html:
                offenders.append(f"{label} still contains {fragment}")

    assert offenders == [], (
        f"'{feature}' is off, but these screens still offer it. A button that "
        f"404s when pressed is a bug even though nothing leaks: {offenders}"
    )


@pytest.mark.parametrize("feature", sorted(GATED))
def test_the_fragments_are_actually_present_while_it_is_on(feature, center):
    """The other half of the matrix.

    Without it, a typo in GATED (or a page that never linked the feature at
    all) would make the test above pass for the wrong reason — and a gate that
    is always closed looks identical to a gate that works.
    """
    tenant, client, student = center
    html = "".join(page for _label, page in visit(client, student))
    assert any(fragment in html for fragment in GATED[feature]), (
        f"'{feature}' is on, but no page links to any of {GATED[feature]} — "
        "so the off-test above proves nothing."
    )
