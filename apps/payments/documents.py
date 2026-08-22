"""Receipt documents.

Only decides *what* is on a receipt; the layout engine in apps.core.pdf owns
how it is drawn, so a receipt and a report share the same typography, header
band, footer stamp and money formatting.
"""

from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.pdf import PdfDocument, document_meta
from apps.core.registry import settings_registry

from .models import PaymentKind

A5 = "A5"
THERMAL = "THERMAL"


def build_receipt(payment, *, request=None, size=A5):
    """One receipt, either A5 (filing) or 80 mm (thermal roll)."""
    charge = payment.monthly_charge
    is_refund = payment.kind == PaymentKind.REFUND

    meta = document_meta(
        request,
        title=_("إيصال استرداد") if is_refund else _("إيصال استلام نقدية"),
        subtitle=f"{_('رقم')} {payment.receipt_number}",
        footer_note=settings_registry.get("center.receipt_footer"),
    )

    doc = PdfDocument(meta, size=size, compact=(size == THERMAL))
    doc.key_values(
        [
            (_("التاريخ"), timezone.localtime(payment.paid_at).strftime("%Y-%m-%d %H:%M")),
            (_("الطالب"), f"{payment.student.full_name} ({payment.student.student_code})"),
            (_("المادة"), f"{charge.grade_subject.subject} — {charge.grade_subject.grade}"),
            (_("الشهر"), charge.billing_month.strftime("%Y-%m")),
            (_("طريقة الدفع"), payment.get_method_display()),
            *([(_("مرجع"), payment.reference)] if payment.reference else []),
        ]
    )
    doc.spacer(10)
    doc.total_line(
        _("المبلغ المسترد") if is_refund else _("المبلغ المدفوع"),
        payment.amount,
        tone="danger" if is_refund else "success",
    )
    doc.spacer(8)
    doc.key_values(
        [
            (_("إجمالي المستحق"), f"{charge.amount_due:,.2f}"),
            *(
                [(_("الخصم"), f"{charge.discount_amount:,.2f}")]
                if charge.discount_amount
                else []
            ),
            (_("إجمالي المسدد"), f"{charge.total_paid:,.2f}"),
            (_("المتبقي"), f"{charge.balance:,.2f}"),
            (_("الحالة"), charge.get_status_display()),
        ]
    )
    if payment.notes:
        doc.spacer(6)
        doc.paragraph(payment.notes, style="small")

    doc.signature_row(
        f"{_('التوقيع')}: ____________",
        f"{_('المحصّل')}: {payment.collected_by.display_name}",
    )
    return doc


def receipt_filename(payment) -> str:
    return f"receipt-{payment.receipt_number}"
