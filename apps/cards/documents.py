"""Card documents: the sheet a print shop works from.

Decides what goes on the sheet; apps.core.pdf owns how it is drawn.
"""

import io

import qrcode
from django.utils.translation import gettext as _

from apps.core.pdf import PdfDocument, document_meta

CARDS_PER_PAGE = 12  # 3 columns × 4 rows on A4


def qr_png(token: str, *, box_size=8) -> bytes:
    """QR for one token. ECC level M scans reliably off a plastic card."""
    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    code.add_data(token)
    code.make(fit=True)
    buffer = io.BytesIO()
    code.make_image(fill_color="black", back_color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def build_card_sheet(cards, *, request=None, batch=""):
    """A4 sheet of QR codes + card numbers, ready for the printer."""
    meta = document_meta(
        request,
        title=_("كشف طباعة البطاقات"),
        subtitle=_("دفعة: %(batch)s") % {"batch": batch or _("بدون")},
        filters=[(_("عدد البطاقات"), str(len(cards)))],
        footer_note=_("لا تُعاد طباعة هذه الرموز — كل رمز يخص بطاقة واحدة"),
    )
    document = PdfDocument(meta, size="A4")
    document.image_grid(
        [
            {"image": qr_png(card.qr_token), "caption": card.card_number, "sub": card.batch}
            for card in cards
        ],
        columns=3,
        image_size=30,
    )
    return document


CARD_SIZE = "CARD"


def build_student_card(card, *, request=None):
    """A print-ready card face (ID-1, 85.6 × 54 mm).

    Deliberately minimal: name, student code and the QR. No phone, no guardian,
    no address — the card is a bearer credential that lives in a teenager's
    pocket, so it must not carry anything worth stealing (docs/06 §I.1).
    """
    student = card.current_student
    meta = document_meta(request, title=_("بطاقة طالب"))
    document = PdfDocument(meta, size=CARD_SIZE)

    lines = [(meta.center_name, "kv_key")]
    if student is not None:
        lines += [(student.full_name, "h2"), (str(student.grade), "small")]
        lines.append((student.student_code, "cell_num"))
    else:
        lines += [(_("بطاقة غير مخصصة"), "h2"), (card.card_number, "cell_num")]

    document.side_by_side(qr_png(card.qr_token, box_size=6), lines, image_size=26)
    document.paragraph(card.card_number, style="small")
    return document


def card_filename(card) -> str:
    student = card.current_student
    return f"card-{student.student_code}" if student else f"card-{card.card_number}"
