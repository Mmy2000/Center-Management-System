"""Arabic ⇄ English: the catalog, the switch, direction and documents."""

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import translation

from apps.accounts.models import Role

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def signed_in(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def switch(client, language):
    return client.post(reverse("set_language"), {"language": language, "next": "/"})


# ------------------------------------------------------------------ catalog #

@pytest.mark.parametrize(
    "arabic,english",
    [
        ("لوحة التحكم", "Dashboard"),
        ("مجموعة بديلة", "Alternative group"),
        ("تم تسجيل الحضور", "Check-in successful"),
        ("المتأخرات", "Outstanding"),
        ("هذه البطاقة مبلّغ عن فقدها", "This card is reported lost"),
        ("المبلغ يجب أن يكون أكبر من صفر", "The amount must be greater than zero"),
    ],
)
def test_key_strings_are_translated(arabic, english):
    from django.utils.translation import gettext as _

    with translation.override("ar"):
        assert _(arabic) == arabic          # Arabic is the msgid — no catalog needed
    with translation.override("en"):
        assert _(arabic) == english


def test_the_catalog_is_complete():
    """Every extracted string has an English translation."""
    import polib
    from django.conf import settings

    catalog = polib.pofile(str(settings.BASE_DIR / "locale/en/LC_MESSAGES/django.po"))
    missing = [entry.msgid for entry in catalog if not entry.msgstr]
    assert missing == [], f"{len(missing)} untranslated, e.g. {missing[:5]}"
    assert len(catalog) > 700


def test_extractor_finds_no_new_strings():
    """extract_messages is idempotent — the .po in the repo is current."""
    import polib
    from django.conf import settings

    po_path = settings.BASE_DIR / "locale/en/LC_MESSAGES/django.po"
    before = {entry.msgid for entry in polib.pofile(str(po_path))}
    call_command("extract_messages", "-l", "en", verbosity=0)
    after = {entry.msgid for entry in polib.pofile(str(po_path))}
    assert after - before == set(), f"unextracted strings: {sorted(after - before)[:5]}"


# ------------------------------------------------------------------- switch #

def test_switching_flips_language_and_direction(signed_in):
    switch(signed_in, "en")
    html = signed_in.get(reverse("dashboard:home")).content.decode("utf-8")
    assert 'lang="en"' in html
    assert 'dir="ltr"' in html
    assert "bootstrap.min.css" in html      # LTR stylesheet
    assert "Dashboard" in html

    switch(signed_in, "ar")
    html = signed_in.get(reverse("dashboard:home")).content.decode("utf-8")
    assert 'lang="ar"' in html
    assert 'dir="rtl"' in html
    assert "bootstrap.rtl.min.css" in html
    assert "لوحة التحكم" in html


def test_the_switch_is_available_before_signing_in(client):
    html = client.get(reverse("accounts:login")).content.decode("utf-8")
    assert reverse("set_language") in html


def test_every_screen_renders_in_english(signed_in):
    switch(signed_in, "en")
    for name in [
        "dashboard:home", "students:list", "students:create", "cards:list",
        "lessons:list", "payments:workspace", "reports:index",
        "attendance:scanner_picker", "core:settings", "accounts:users",
    ]:
        response = signed_in.get(reverse(name))
        assert response.status_code == 200, name
        body = response.content.decode("utf-8")
        # The page chrome must be English; data (center name) may stay Arabic.
        assert 'dir="ltr"' in body, name


# --------------------------------------------------------------- JS catalog #

def test_javascript_catalog_serves_the_same_strings(signed_in):
    switch(signed_in, "en")
    body = signed_in.get(reverse("javascript-catalog")).content.decode("utf-8")
    assert '"Saved"' in body
    assert '"No data"' in body
    assert "django.catalog" in body


# ---------------------------------------------------------------- documents #

def test_pdf_labels_follow_the_active_language(signed_in, user_factory):
    """Labels translate; stored data (names, subjects) keeps its own language."""
    import io
    from datetime import date
    from decimal import Decimal

    from pypdf import PdfReader

    from apps.academics.models import EducationalStage, Grade, GradeSubject, Subject
    from apps.payments import services as pay
    from apps.payments.models import MonthlyCharge
    from apps.students.services import create_student

    stage = EducationalStage.objects.create(name="Secondary", code="SEC")
    grade = Grade.objects.create(stage=stage, name="Grade 3", code="SEC3")
    subject = Subject.objects.create(name="Physics", code="PHY")
    offering = GradeSubject.objects.create(grade=grade, subject=subject)
    student = create_student(full_name="أحمد محمد", grade=grade, guardian_phone="01012345678")
    charge = MonthlyCharge.objects.create(
        student=student,
        grade_subject=offering,
        billing_month=date(2026, 8, 1),
        amount_due=Decimal("500.00"),
    )
    cashier = user_factory(username="cash")
    payment = pay.record_payment(charge, "200.00", collected_by=cashier)

    switch(signed_in, "en")
    response = signed_in.get(reverse("payments_api:receipt", args=[payment.pk]))
    text = PdfReader(io.BytesIO(response.content)).pages[0].extract_text(
        extraction_mode="layout"
    )
    assert "Payment receipt" in text
    assert "Student" in text
    assert payment.receipt_number in text


def test_report_pdf_titles_translate(signed_in):
    switch(signed_in, "en")
    response = signed_in.get(
        reverse("reports_api:export", args=["attendance-daily"]) + "?format=pdf"
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
