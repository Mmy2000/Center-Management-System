"""The PDF engine: every document in the system goes through it."""

import pytest
from reportlab.lib import colors

from apps.core.pdf import (
    DocumentMeta,
    PdfDocument,
    ar,
    document_meta,
    fmt_money,
    register_fonts,
)

# Documents resolve the center's palette from the settings registry, so every
# test in this module needs a database.
pytestmark = pytest.mark.django_db


def make_doc(**kwargs):
    meta = DocumentMeta(title="تقرير تجريبي", center_name="سنتر التفوق", **kwargs)
    return PdfDocument(meta)


def test_fonts_register_once():
    assert register_fonts() is True
    assert register_fonts() is True  # cached, no double registration


def test_arabic_is_reshaped_and_reordered():
    shaped = ar("أحمد محمد")
    assert shaped != "أحمد محمد"  # glyphs were joined
    assert len(shaped) <= len("أحمد محمد")  # ligatures may shorten it


def test_latin_text_is_left_alone():
    assert ar("STD-000123") == "STD-000123"
    assert ar("500.00") == "500.00"
    assert ar("") == ""
    assert ar(None) == ""


def test_money_formatting_is_latin_with_two_decimals():
    assert fmt_money("1234.5") == "1,234.50"
    assert fmt_money(0) == "0.00"
    assert fmt_money(None) == "0.00"


def test_document_produces_a_valid_pdf_with_embedded_fonts():
    doc = make_doc(subtitle="اختبار", generated_by="admin")
    doc.heading("قسم")
    doc.paragraph("سطر عربي مع رقم 500")
    doc.table(
        [{"key": "name", "label": "الاسم"}, {"key": "amount", "label": "المبلغ", "money": True}],
        [{"name": "أحمد محمد", "amount": "500"}, {"name": "سارة علي", "amount": "300.5"}],
        totals={"amount": "800.50"},
    )
    pdf = doc.build()

    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert b"Amiri" in pdf, "the Arabic face must be embedded, not referenced"
    assert len(pdf) > 5000


def test_empty_table_does_not_crash():
    doc = make_doc()
    doc.table([{"key": "a", "label": "أ"}], [], empty_message="لا توجد بيانات")
    assert doc.build().startswith(b"%PDF-")


def test_key_values_and_totals_render():
    doc = make_doc()
    doc.key_values([("الطالب", "أحمد محمد"), ("الكود", "STD-000001")])
    doc.total_line("المبلغ المدفوع", "250.00", tone="success")
    doc.signature_row("التوقيع: ____", "المحصّل: admin")
    assert len(doc.build()) > 3000


@pytest.mark.parametrize("size", ["A4", "A5", "THERMAL"])
def test_every_page_size_builds(size):
    meta = DocumentMeta(title="إيصال", center_name="سنتر")
    doc = PdfDocument(meta, size=size)
    doc.key_values([("المبلغ", "100.00")])
    assert doc.build().startswith(b"%PDF-")


def test_landscape_is_wider_than_portrait():
    meta = DocumentMeta(title="تقرير")
    portrait = PdfDocument(meta, size="A4")
    landscape = PdfDocument(meta, size="A4", landscape_mode=True)
    assert landscape.content_width > portrait.content_width


def test_response_is_a_download_not_an_inline_preview():
    doc = make_doc()
    doc.paragraph("محتوى")
    response = doc.response("receipt-R-000001")

    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == 'attachment; filename="receipt-R-000001.pdf"'
    assert response["X-Content-Type-Options"] == "nosniff"
    assert int(response["Content-Length"]) == len(response.content)


def test_inline_is_available_but_not_the_default():
    doc = make_doc()
    doc.paragraph("محتوى")
    assert doc.response("x", inline=True)["Content-Disposition"].startswith("inline;")


def test_document_meta_reads_the_center_identity(rf, user_factory):
    from apps.core.registry import settings_registry

    settings_registry.set("center.name", "سنتر النجاح")
    settings_registry.set("center.phone", "01012345678")

    request = rf.get("/")
    request.user = user_factory(username="boss", full_name="مدير النظام")

    meta = document_meta(request, title="تقرير", filters=[("الشهر", "2026-08")])
    assert meta.center_name == "سنتر النجاح"
    assert meta.center_phone == "01012345678"
    assert meta.generated_by == "مدير النظام"
    assert meta.filters == [("الشهر", "2026-08")]


def test_arabic_reads_correctly_out_of_the_finished_pdf():
    """Guards against the classic double-bidi bug.

    ``ar()`` shapes and reverses once; if ReportLab's own RTL wrapping were also
    enabled the text would come back reversed and unreadable. pypdf undoes the
    visual ordering when it extracts, so a correct document yields the shaped
    (logical) string back.
    """
    import io

    import arabic_reshaper
    from pypdf import PdfReader

    name = "أحمد محمد علي"
    doc = make_doc()
    doc.paragraph(name)
    doc.table(
        [{"key": "student", "label": "الطالب"}],
        [{"student": name}],
    )

    extracted = PdfReader(io.BytesIO(doc.build())).pages[0].extract_text()
    assert arabic_reshaper.reshape(name) in extracted


def test_mixed_arabic_and_latin_survives_on_the_page():
    """A receipt number sits inside an Arabic sentence — both runs must render.

    pypdf's default extraction drops one direction of a mixed line, so this uses
    layout mode, which reads the page as drawn.
    """
    import io

    from pypdf import PdfReader

    doc = make_doc()
    doc.paragraph("رقم الإيصال R-000123")
    doc.total_line("المبلغ المدفوع", "1250.75")

    text = PdfReader(io.BytesIO(doc.build())).pages[0].extract_text(extraction_mode="layout")
    assert "R-000123" in text
    assert "1,250.75" in text


# ------------------------------------------------------------ palette link #


def test_documents_follow_the_center_palette():
    """Change the theme in Settings and the paper changes with the screen."""
    from apps.core.pdf import brand_colors
    from apps.core.registry import settings_registry

    settings_registry.set("ui.theme", "teal")
    teal, teal_dark, teal_light = brand_colors()

    settings_registry.set("ui.theme", "violet")
    violet, violet_dark, violet_light = brand_colors()

    assert teal.hexval() != violet.hexval()
    assert teal_dark.hexval() != violet_dark.hexval()

    doc_teal = PdfDocument(DocumentMeta(title="تقرير"), size="A4")
    settings_registry.set("ui.theme", "custom")
    settings_registry.set("ui.accent", "#b5179e")
    doc_custom = PdfDocument(DocumentMeta(title="تقرير"), size="A4")

    assert doc_teal.brand.hexval() != doc_custom.brand.hexval()
    assert doc_custom.brand.hexval() == colors.HexColor("#b5179e").hexval()


def test_an_invalid_accent_falls_back_instead_of_crashing():
    from apps.core.color import normalize_hex, readable_on, shade
    from apps.core.pdf import brand_colors
    from apps.core.registry import settings_registry

    assert normalize_hex("nonsense") == "#1f6f8b"
    assert normalize_hex("b5179e") == "#b5179e"
    assert shade("#ffffff", -1.0) == "#000000"
    assert readable_on("#ffffff") == "#000000"
    assert readable_on("#0a2833") == "#ffffff"

    settings_registry.set("ui.theme", "custom")
    settings_registry.set("ui.accent", "#1f6f8b")
    assert brand_colors()[0].hexval() == colors.HexColor("#1f6f8b").hexval()
