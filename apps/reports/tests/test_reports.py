"""Report reconciliation and export tests (TASK-072 → 075)."""

import csv
import io
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.academics.models import EducationalStage, Grade, GradeSubject, Group, Subject
from apps.accounts.models import Role
from apps.attendance import services as att
from apps.attendance.models import Attendance, AttendanceState, AttendanceStatus
from apps.cards.models import StudentCard
from apps.cards.services import assign_card
from apps.lessons.services import complete_lesson, create_lesson, open_lesson
from apps.payments import services as pay
from apps.payments.models import MonthlyCharge, Payment, PaymentKind
from apps.reports import registry
from apps.reports.queries import dashboard_summary, group_dashboard
from apps.students import assignment_services as assign_svc
from apps.students.services import create_student

pytestmark = pytest.mark.django_db
PASSWORD = "TestPass!2026"


@pytest.fixture
def center(db):
    """One group, three students, one completed lesson, some money."""
    call_command("seed_roles", verbosity=0)
    stage = EducationalStage.objects.create(name="Secondary", code="SEC")
    grade = Grade.objects.create(stage=stage, name="Grade 3 Secondary", code="SEC3")
    subject = Subject.objects.create(name="Physics", code="PHY")
    offering = GradeSubject.objects.create(
        grade=grade, subject=subject, default_monthly_fee=Decimal("500.00")
    )
    group_a = Group.objects.create(
        grade_subject=offering, name="Group A", code="SEC3-PHY-A", monthly_fee=Decimal("500.00")
    )
    group_b = Group.objects.create(
        grade_subject=offering, name="Group B", code="SEC3-PHY-B", monthly_fee=Decimal("500.00")
    )

    students, cards = [], []
    for index in range(3):
        student = create_student(
            full_name=f"طالب {index}", grade=grade, guardian_phone="0101234567" + str(index)
        )
        card = StudentCard.objects.create(
            card_number=f"CARD-{index:06d}", qr_token=f"CMS1:token{index:016d}"
        )
        assign_card(card, student)
        assign_svc.assign_student(student, group_a)
        students.append(student)
        cards.append(card)

    start = timezone.now()
    lesson = open_lesson(create_lesson(group_a, start, start + timedelta(hours=2)))
    att.scan(lesson_id=lesson.pk, qr_token=cards[0].qr_token)  # present

    # Student 1 attends Group B instead (alternative group).
    other = open_lesson(create_lesson(group_b, start, start + timedelta(hours=2)))
    att.scan(lesson_id=other.pk, qr_token=cards[1].qr_token)

    complete_lesson(lesson)  # students 1 and 2 become absent in Group A

    return {
        "grade": grade,
        "subject": subject,
        "offering": offering,
        "group_a": group_a,
        "group_b": group_b,
        "students": students,
        "cards": cards,
        "lesson": lesson,
    }


@pytest.fixture
def money(center, user_factory):
    cashier = user_factory(username="cash", role=Role.CASHIER)
    pay.generate_monthly_charges(timezone.localdate())
    charge = MonthlyCharge.objects.filter(student=center["students"][0]).first()
    pay.record_payment(charge, "200.00", collected_by=cashier)
    return {"cashier": cashier, "charge": charge}


@pytest.fixture
def admin_client_(client, user_factory):
    call_command("seed_roles", verbosity=0)
    user_factory(username="boss", role=Role.SUPER_ADMIN, password=PASSWORD)
    client.login(username="boss", password=PASSWORD)
    return client


def run(slug, **filters):
    return registry.get(slug).query(filters)


# ------------------------------------------------------------------ registry #

def test_every_report_declares_columns_and_a_permission():
    reports = registry.all_reports()
    assert len(reports) >= 14
    for report in reports:
        assert report.columns, report.slug
        assert report.permission in {
            "reports.view_reports",
            "reports.view_financial_reports",
        }
        assert callable(report.query)
        for key in report.totals:
            assert any(c.key == key for c in report.columns), f"{report.slug}:{key}"


def test_unknown_report_raises():
    with pytest.raises(KeyError):
        registry.get("no-such-report")


# ------------------------------------------------- attendance reconciliation #

def test_daily_report_matches_the_attendance_rows(center):
    rows = run("attendance-daily")
    today = timezone.localdate().isoformat()
    row = next(r for r in rows if r["date"] == today)

    # "Present" counts PRESENT and PARTIAL: a student who left early still
    # attended (auto-checkout marks a short stay PARTIAL).
    assert row["present"] == Attendance.objects.filter(
        status__in=[AttendanceStatus.PRESENT, AttendanceStatus.PARTIAL]
    ).count()
    assert row["absent"] == Attendance.objects.filter(state=AttendanceState.ABSENT).count()
    assert row["alternative"] == 1


def test_lesson_report_totals_match(center):
    rows = run("attendance-lesson")
    group_a_row = next(r for r in rows if r["group"] == "Group A")
    assert group_a_row["present"] == 1
    assert group_a_row["absent"] == 2
    assert group_a_row["expected"] == 3


def test_student_report_rate(center):
    rows = run("attendance-student")
    by_name = {row["student"]: row for row in rows}
    assert by_name["طالب 0"]["present"] == 1
    assert by_name["طالب 0"]["rate"] == "100"
    assert by_name["طالب 2"]["absent"] == 1
    assert by_name["طالب 2"]["rate"] == "0"


def test_alternative_group_report_shows_both_groups(center):
    rows = run("alternative-groups")
    assert len(rows) == 1
    row = rows[0]
    assert row["assigned_group"] == "Group A"
    assert row["attended_group"] == "Group B"
    assert row["times_this_month"] == 1


def test_alternative_report_filters_by_type(center):
    assert run("alternative-groups", type="MAKEUP") == []
    assert len(run("alternative-groups", type="ALTERNATIVE_GROUP")) == 1


def test_absent_report_carries_the_guardian_phone(center):
    rows = run("absent-students")
    assert len(rows) == 2
    assert all(row["guardian_phone"].startswith("0101") for row in rows)


def test_late_report_is_empty_without_late_students(center):
    assert run("late-students") == []


# ---------------------------------------------------- finance reconciliation #

def test_collection_report_matches_the_ledger(center, money):
    rows = run("collection-daily")
    assert rows[0]["collected"] == "200.00"
    assert rows[0]["net"] == "200.00"

    ledger = sum(
        p.signed_amount for p in Payment.objects.filter(kind=PaymentKind.PAYMENT)
    )
    assert Decimal(rows[0]["collected"]) == ledger


def test_outstanding_report_lists_only_unsettled_charges(center, money):
    rows = run("outstanding", month=timezone.localdate().replace(day=1))
    balances = {row["student"]: Decimal(row["balance"]) for row in rows}

    assert balances["طالب 0"] == Decimal("300.00")
    assert sum(balances.values()) == MonthlyCharge.objects.outstanding().count() * Decimal("0") + sum(
        c.balance for c in MonthlyCharge.objects.outstanding()
    )


def test_finance_by_group_rate(center, money):
    rows = run("finance-group", month=timezone.localdate().replace(day=1))
    row = next(r for r in rows if r["group"] == "Group A")
    assert row["expected"] == "1500.00"
    assert row["collected"] == "200.00"
    assert row["outstanding"] == "1300.00"
    assert row["rate"] == "13"


def test_waived_charges_are_excluded_from_finance_reports(center, money, user_factory):
    charge = MonthlyCharge.objects.exclude(pk=money["charge"].pk).first()
    pay.waive_charge(charge, actor=money["cashier"], reason="منحة")

    rows = run("finance-subject", month=timezone.localdate().replace(day=1))
    assert rows[0]["expected"] == "1000.00"  # 1500 − the waived 500


def test_cashier_daybook_splits_by_method(center, money):
    pay.record_payment(
        money["charge"], "100.00", collected_by=money["cashier"], method="WALLET"
    )
    rows = run("cashier-daybook")
    methods = {row["method"]: row for row in rows}
    assert len(methods) == 2
    assert sum(Decimal(row["collected"]) for row in rows) == Decimal("300.00")


def test_refunds_reduce_the_net_but_keep_the_gross(center, money):
    payment = Payment.objects.filter(kind=PaymentKind.PAYMENT).first()
    pay.refund_payment(payment, actor=money["cashier"], reason="خطأ")

    rows = run("collection-daily")
    assert rows[0]["collected"] == "200.00"
    assert rows[0]["refunded"] == "200.00"
    assert rows[0]["net"] == "0.00"


# ------------------------------------------------------------------ dashboard #

def test_dashboard_numbers_reconcile(center, money, user_factory):
    boss = user_factory(username="boss2", role=Role.SUPER_ADMIN)
    data = dashboard_summary(boss)

    assert data["today"]["present"] == Attendance.objects.filter(
        status__in=[AttendanceStatus.PRESENT, AttendanceStatus.PARTIAL]
    ).count()
    assert data["today"]["absent"] == Attendance.objects.filter(
        state=AttendanceState.ABSENT
    ).count()
    assert data["month"]["collected"] == "200.00"
    assert data["month"]["expected"] == "1500.00"
    assert data["month"]["outstanding"] == "1300.00"


def test_group_dashboard(center, money):
    data = group_dashboard(center["group_a"])
    assert data["students"] == 3
    assert data["attendance_30d"]["present"] == 1
    assert data["attendance_30d"]["absent"] == 2
    assert data["money"]["expected"] == "1500.00"


# ------------------------------------------------------------------ endpoints #

def test_report_endpoint_returns_columns_rows_and_totals(admin_client_, center):
    response = admin_client_.get(reverse("reports_api:report_data", args=["attendance-daily"]))
    assert response.status_code == 200
    data = response.json()["data"]
    assert [c["key"] for c in data["columns"]][:2] == ["date", "lessons"]
    assert data["count"] >= 1
    assert "present" in data["totals"]
    assert data["truncated"] is False


def test_report_endpoint_rejects_a_bad_date(admin_client_):
    response = admin_client_.get(
        reverse("reports_api:report_data", args=["attendance-daily"]) + "?from=22-08-2026"
    )
    assert response.status_code == 400
    assert "from" in response.json()["field_errors"]


def test_csv_export_is_excel_friendly_arabic(admin_client_, center):
    response = admin_client_.get(
        reverse("reports_api:export", args=["alternative-groups"]) + "?format=csv"
    )
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith("attachment;")

    body = response.content.decode("utf-8")
    assert body.startswith("﻿")  # BOM for Excel
    rows = list(csv.reader(io.StringIO(body.lstrip("﻿"))))
    assert rows[0][0] == "التاريخ"
    assert any("Group B" in cell for cell in rows[1])


def test_export_is_audited(admin_client_, center):
    from apps.core.models import AuditAction, AuditLog

    admin_client_.get(reverse("reports_api:export", args=["attendance-daily"]))
    assert AuditLog.objects.filter(action=AuditAction.REPORT_EXPORTED).exists()


def test_instructor_cannot_open_financial_reports(client, user_factory, center):
    user_factory(username="teach", role=Role.INSTRUCTOR, password=PASSWORD)
    client.login(username="teach", password=PASSWORD)

    assert client.get(reverse("reports_api:report_data", args=["attendance-daily"])).status_code == 200
    assert client.get(reverse("reports_api:report_data", args=["outstanding"])).status_code == 403


def test_cashier_sees_only_financial_reports(client, user_factory, center):
    user_factory(username="cash2", role=Role.CASHIER, password=PASSWORD)
    client.login(username="cash2", password=PASSWORD)

    listing = client.get(reverse("reports_api:report_list")).json()["data"]["results"]
    assert {row["group"] for row in listing} == {"finance"}


def test_pages_render(admin_client_, center):
    assert admin_client_.get(reverse("reports:index")).status_code == 200
    assert admin_client_.get(reverse("reports:report", args=["outstanding"])).status_code == 200
    assert admin_client_.get(reverse("dashboard:home")).status_code == 200


# ------------------------------------------------------------ PDF documents #

def test_pdf_export_is_a_download(admin_client_, center):
    response = admin_client_.get(
        reverse("reports_api:export", args=["attendance-daily"]) + "?format=pdf"
    )
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("attachment;")
    assert response.content.startswith(b"%PDF-")
    assert b"Amiri" in response.content  # Arabic face embedded
    assert len(response.content) > 4000


def test_every_report_can_be_exported_as_pdf(admin_client_, center, money):
    for report in registry.all_reports():
        response = admin_client_.get(
            reverse("reports_api:export", args=[report.slug]) + "?format=pdf"
        )
        assert response.status_code == 200, report.slug
        assert response.content.startswith(b"%PDF-"), report.slug


def test_pdf_export_is_audited_like_the_others(admin_client_, center):
    from apps.core.models import AuditAction, AuditLog

    admin_client_.get(reverse("reports_api:export", args=["outstanding"]) + "?format=pdf")
    entry = AuditLog.objects.filter(action=AuditAction.REPORT_EXPORTED).latest("created_at")
    assert entry.changes["format"] == "pdf"


def test_export_requires_the_export_permission(client, user_factory, center):
    user_factory(username="teach3", role=Role.INSTRUCTOR, password=PASSWORD)
    client.login(username="teach3", password=PASSWORD)
    response = client.get(
        reverse("reports_api:export", args=["attendance-daily"]) + "?format=pdf"
    )
    assert response.status_code == 403
