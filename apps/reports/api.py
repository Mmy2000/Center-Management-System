"""Report endpoints and exporters (TASK-071 → 075)."""

import csv
import io
from datetime import datetime

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.academics.models import EducationalStage, Grade, Group, Instructor, Subject
from apps.accounts.decorators import require_perm
from apps.core.audit import record
from apps.core.http import DomainError, ajax
from apps.core.models import AuditAction
from apps.core.pdf import PdfDocument, document_meta
from apps.payments.services import normalize_month

from . import registry
from .queries import dashboard_summary, group_dashboard

MAX_ROWS = 5000
FILTER_KEYS = (
    "from", "to", "month", "stage", "grade", "subject", "group",
    "instructor", "student", "method", "cashier", "type", "status",
)


def parse_filters(params) -> dict:
    filters: dict = {}
    for key in FILTER_KEYS:
        value = params.get(key)
        if not value:
            continue
        if key in ("from", "to"):
            try:
                filters[key] = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError as exc:
                raise DomainError(
                    "ERR_VALIDATION",
                    _("تاريخ غير صحيح"),
                    field_errors={key: [_("صيغة التاريخ YYYY-MM-DD")]},
                ) from exc
        elif key == "month":
            filters[key] = normalize_month(value)
        else:
            filters[key] = value
    return filters


def _get_report(request, slug):
    try:
        report = registry.get(slug)
    except KeyError as exc:
        raise DomainError("ERR_NOT_FOUND", _("تقرير غير معروف"), status=404) from exc
    if not request.user.has_perm(report.permission):
        raise DomainError("ERR_FORBIDDEN", _("لا تملك صلاحية هذا التقرير"), status=403)
    return report


def _totals(report, rows) -> dict:
    from decimal import Decimal

    totals = {}
    for key in report.totals:
        column = next((c for c in report.columns if c.key == key), None)
        if column and column.money:
            total = sum((Decimal(str(row.get(key, 0) or 0)) for row in rows), Decimal("0.00"))
            totals[key] = str(total.quantize(Decimal("0.01")))
        else:
            totals[key] = sum(int(row.get(key, 0) or 0) for row in rows)
    return totals


@ajax(methods=["GET"])
def report_data(request, slug):
    report = _get_report(request, slug)
    rows = report.query(parse_filters(request.GET))
    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]

    return {
        "slug": report.slug,
        "title": str(report.title),
        "columns": [
            {"key": c.key, "label": str(c.label), "money": c.money} for c in report.columns
        ],
        "rows": rows,
        "count": len(rows),
        "totals": _totals(report, rows),
        # Never truncate silently (docs/06 §J.3).
        "truncated": truncated,
        "truncation_note": (
            _("عرض أول %(n)s صف فقط — ضيّق الفلاتر أو صدّر الملف") % {"n": MAX_ROWS}
            if truncated
            else ""
        ),
    }


@ajax(methods=["GET"])
def report_list(request):
    reports = registry.for_user(request.user)
    return {
        "results": [
            {
                "slug": r.slug,
                "title": str(r.title),
                "group": r.group,
                "description": str(r.description),
            }
            for r in reports
        ]
    }


@require_perm("reports.export_reports")
def export(request, slug):
    report = _get_report(request, slug)
    fmt = (request.GET.get("format") or "csv").lower()
    rows = report.query(parse_filters(request.GET))

    record(
        AuditAction.REPORT_EXPORTED,
        changes={"report": slug, "format": fmt, "rows": len(rows)},
        object_repr=f"export:{slug}",
        actor=request.user,
    )

    filename = f"{slug}-{timezone.localdate():%Y%m%d}"
    if fmt == "xlsx":
        return _xlsx_response(report, rows, filename)
    if fmt == "pdf":
        return _pdf_response(request, report, rows, filename)
    return _csv_response(report, rows, filename)


def _filter_summary(request, report) -> list[tuple[str, str]]:
    """Human-readable echo of the filters, printed under the report title."""
    labels = {
        "from": _("من"), "to": _("إلى"), "month": _("الشهر"), "stage": _("المرحلة"),
        "grade": _("الصف"), "subject": _("المادة"), "group": _("المجموعة"),
        "instructor": _("المدرّس"), "student": _("الطالب"), "method": _("طريقة الدفع"),
        "cashier": _("المحصّل"), "type": _("النوع"), "status": _("الحالة"),
    }
    names = {
        "stage": EducationalStage, "grade": Grade, "subject": Subject,
        "group": Group, "instructor": Instructor,
    }
    summary = []
    for key in report.filters:
        value = request.GET.get(key)
        if not value:
            continue
        model = names.get(key)
        if model is not None:
            obj = model.objects.filter(pk=value).first()
            value = str(obj) if obj else value
        summary.append((str(labels.get(key, key)), str(value)))
    return summary


def _pdf_response(request, report, rows, filename):
    """Landscape A4 for wide tables, portrait for narrow ones."""
    meta = document_meta(
        request,
        title=str(report.title),
        subtitle=str(report.description),
        filters=_filter_summary(request, report),
    )
    columns = [{"key": c.key, "label": str(c.label), "money": c.money} for c in report.columns]
    document = PdfDocument(meta, size="A4", landscape_mode=len(columns) > 6)
    document.table(columns, rows, totals=_totals(report, rows) or None)
    document.spacer(8)
    document.paragraph(
        str(_("عدد الصفوف: %(n)s") % {"n": len(rows)}),
        style="small",
    )
    return document.response(filename)


def _csv_response(report, rows, filename) -> HttpResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([str(c.label) for c in report.columns])
    for row in rows:
        writer.writerow([row.get(c.key, "") for c in report.columns])

    # UTF-8 BOM so Excel opens Arabic correctly without an import wizard.
    content = "﻿" + buffer.getvalue()
    response = HttpResponse(content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    return response


def _xlsx_response(report, rows, filename) -> HttpResponse:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:  # openpyxl ships in requirements/prod.txt
        raise DomainError(
            "ERR_EXPORT_UNAVAILABLE",
            _("تصدير Excel غير متاح على هذا الخادم — استخدم CSV"),
            status=503,
        ) from exc

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = report.slug[:31]
    sheet.sheet_view.rightToLeft = True

    sheet.append([str(c.label) for c in report.columns])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append([row.get(c.key, "") for c in report.columns])

    buffer = io.BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    return response


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

@require_perm("reports.view_reports", "reports.view_financial_reports", any_of=True)
def reports_page(request, slug=None):
    reports = registry.for_user(request.user)
    if slug:
        report = _get_report(request, slug)
    else:
        report = reports[0] if reports else None

    return render(
        request,
        "reports/report.html",
        {
            "reports": reports,
            "report": report,
            "filters": report.filters if report else (),
            "stages": EducationalStage.objects.filter(is_active=True),
            "grades": Grade.objects.filter(is_active=True).select_related("stage"),
            "subjects": Subject.objects.filter(is_active=True),
            "groups": Group.objects.select_related("grade_subject__subject"),
            "instructors": Instructor.objects.filter(is_active=True),
            "today": timezone.localdate().isoformat(),
            "month_start": timezone.localdate().replace(day=1).isoformat(),
        },
    )


@ajax(methods=["GET"])
def dashboard(request):
    return dashboard_summary(request.user)


@ajax(methods=["GET"], perm="academics.view_group")
def group_stats(request, pk):
    from apps.accounts.scoping import visible_groups

    group = get_object_or_404(visible_groups(request.user, Group.objects.all()), pk=pk)
    return group_dashboard(group)
