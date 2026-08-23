"""PDF document engine (single source for every PDF in the system).

Why ReportLab and not HTML→PDF: WeasyPrint needs GTK/Pango native libraries
that are painful on Windows, and headless Chromium is a 300 MB dependency for
a receipt. ReportLab is pure Python and deterministic; Arabic is handled by
reshaping + bidi before the text reaches the canvas, with a bundled Amiri face
so output is identical on every machine.

Everything a PDF needs lives here — fonts, shaping, page furniture, tables,
key/value blocks, totals. A document module (receipt, report, …) only decides
*what* goes on the page, never *how* it is drawn.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #

FONT_REGULAR = "Amiri"
FONT_BOLD = "Amiri-Bold"
FONT_DIR = Path(settings.BASE_DIR) / "static" / "fonts"

# Palette presets, mirrored from static/css/themes.css: paper matches screen,
# including when an admin picks a different palette for the center.
THEME_ACCENTS = {
    "teal": "#1f6f8b",
    "indigo": "#4149b8",
    "violet": "#7442ad",
    "emerald": "#1f855c",
    "sunset": "#b9571e",
    "slate": "#4b5d70",
}

# Fallbacks; brand_colors() resolves the live values per document.
BRAND = colors.HexColor("#1f6f8b")
BRAND_DARK = colors.HexColor("#0e3746")
BRAND_LIGHT = colors.HexColor("#eef6f8")
INK = colors.HexColor("#10202a")
INK_SOFT = colors.HexColor("#5d7078")
LINE = colors.HexColor("#dbe4e8")
ZEBRA = colors.HexColor("#f7fafb")
SUCCESS = colors.HexColor("#12805c")
DANGER = colors.HexColor("#c2374b")

THERMAL = (80 * mm, 297 * mm)  # 80 mm roll, cut to content length
CARD = (85.6 * mm, 54 * mm)  # ID-1, what a card printer expects


@lru_cache(maxsize=1)
def register_fonts() -> bool:
    """Register the bundled Arabic faces once per process."""
    pdfmetrics.registerFont(TTFont(FONT_REGULAR, str(FONT_DIR / "Amiri-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BOLD, str(FONT_DIR / "Amiri-Bold.ttf")))
    pdfmetrics.registerFontFamily(FONT_REGULAR, normal=FONT_REGULAR, bold=FONT_BOLD)
    return True


def ar(text) -> str:
    """Shape + bidi-order a string so ReportLab draws readable Arabic.

    Latin-only strings (codes, amounts) are returned untouched — reshaping them
    would reverse them.
    """
    if text is None:
        return ""
    text = str(text)
    if not text:
        return ""
    if not any("؀" <= ch <= "ۿ" for ch in text):
        return text
    return get_display(arabic_reshaper.reshape(text))


def fmt_money(value) -> str:
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return str(value)
    return f"{amount:,.2f}"


# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #


def _style(name, size, *, bold=False, align=TA_RIGHT, colour=INK, leading=None, space=0):
    return ParagraphStyle(
        name,
        fontName=FONT_BOLD if bold else FONT_REGULAR,
        fontSize=size,
        leading=leading or size * 1.55,
        alignment=align,
        textColor=colour,
        spaceAfter=space,
        # No wordWrap="RTL" here: ar() has already reshaped and bidi-ordered the
        # text, and ReportLab's RTL wrapping would reverse it a second time.
    )


STYLES = {
    "title": _style("title", 16, bold=True, align=TA_CENTER, colour=BRAND_DARK),
    "subtitle": _style("subtitle", 9.5, align=TA_CENTER, colour=INK_SOFT),
    "h2": _style("h2", 12, bold=True, colour=BRAND_DARK, space=4),
    "body": _style("body", 9.5),
    "small": _style("small", 8, colour=INK_SOFT),
    "cell": _style("cell", 8.5, leading=12),
    "cell_bold": _style("cell_bold", 8.5, bold=True, leading=12),
    "cell_num": _style("cell_num", 8.5, align=TA_LEFT, leading=12),
    "head": _style("head", 8.5, bold=True, align=TA_CENTER, colour=colors.white, leading=12),
    "kv_key": _style("kv_key", 9, bold=True, colour=INK_SOFT),
    "kv_val": _style("kv_val", 9.5),
    "total": _style("total", 13, bold=True, colour=BRAND_DARK),
}


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #


def brand_colors() -> tuple:
    """(brand, dark, light) for the center's configured palette."""
    from .color import normalize_hex, shade
    from .registry import settings_registry

    theme = settings_registry.get("ui.theme")
    accent = (
        settings_registry.get("ui.accent")
        if theme == "custom"
        else THEME_ACCENTS.get(theme, THEME_ACCENTS["teal"])
    )
    accent = normalize_hex(accent)
    return (
        colors.HexColor(accent),
        colors.HexColor(shade(accent, -0.47)),
        colors.HexColor(shade(accent, 0.94)),
    )


@dataclass
class DocumentMeta:
    """Everything the page furniture needs — never fetched inside the engine."""

    title: str
    subtitle: str = ""
    center_name: str = ""
    center_phone: str = ""
    center_address: str = ""
    generated_by: str = ""
    footer_note: str = ""
    filters: list[tuple[str, str]] = field(default_factory=list)


class PdfDocument:
    """Builds one PDF. Callers append content; the engine owns the chrome.

    Usage:
        doc = PdfDocument(meta, size="A4", landscape_mode=True)
        doc.table(columns, rows, totals=…)
        return doc.response("attendance-daily")
    """

    def __init__(self, meta: DocumentMeta, *, size="A4", landscape_mode=False, compact=False):
        register_fonts()
        self.meta = meta
        self.compact = compact or size in ("THERMAL", "CARD")
        self.pagesize = {"A4": A4, "A5": A5, "THERMAL": THERMAL, "CARD": CARD}[size]
        if landscape_mode:
            self.pagesize = landscape(self.pagesize)
        self.margin = {"THERMAL": 6 * mm, "CARD": 4 * mm}.get(size, 14 * mm)
        self.chromeless = size == "CARD"  # no header band, no page footer
        self.brand, self.brand_dark, self.brand_light = brand_colors()
        self.story: list = []
        self._buffer = io.BytesIO()

    # ------------------------------------------------------------ content --
    def spacer(self, height=4):
        self.story.append(Spacer(1, height))
        return self

    def heading(self, text):
        self.story.append(Paragraph(ar(text), STYLES["h2"]))
        return self

    def paragraph(self, text, style="body"):
        self.story.append(Paragraph(ar(text), STYLES[style]))
        return self

    def key_values(self, rows, *, widths=None):
        """Two-column label/value block (receipts, summaries)."""
        data = [
            [
                Paragraph(ar(str(value)), STYLES["kv_val"]),
                Paragraph(ar(str(label)), STYLES["kv_key"]),
            ]
            for label, value in rows
        ]
        available = self.content_width
        widths = widths or [available * 0.6, available * 0.4]
        table = Table(data, colWidths=widths, hAlign="RIGHT")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        self.story.append(table)
        return self

    def table(self, columns, rows, *, totals=None, col_widths=None, empty_message="لا توجد بيانات"):
        """Data table. ``columns`` is a list of dicts: {key, label, money?}.

        Columns are emitted right-to-left, which is what an Arabic reader
        expects: the first declared column lands on the right edge.
        """
        if not rows:
            self.story.append(Paragraph(ar(empty_message), STYLES["small"]))
            return self

        header = [Paragraph(ar(c["label"]), STYLES["head"]) for c in columns][::-1]
        body = []
        for row in rows:
            cells = []
            for column in columns:
                value = row.get(column["key"], "")
                if column.get("money"):
                    cells.append(Paragraph(fmt_money(value), STYLES["cell_num"]))
                else:
                    text = "—" if value in (None, "") else str(value)
                    cells.append(Paragraph(ar(text), STYLES["cell"]))
            body.append(cells[::-1])

        data = [header] + body
        if totals:
            footer = []
            for index, column in enumerate(columns):
                if index == 0:
                    footer.append(Paragraph(ar("الإجمالي"), STYLES["cell_bold"]))
                elif column["key"] in totals:
                    value = totals[column["key"]]
                    footer.append(
                        Paragraph(
                            fmt_money(value) if column.get("money") else str(value),
                            STYLES["cell_num"] if column.get("money") else STYLES["cell_bold"],
                        )
                    )
                else:
                    footer.append(Paragraph("", STYLES["cell"]))
            data.append(footer[::-1])

        widths = col_widths or [self.content_width / len(columns)] * len(columns)
        table = Table(data, colWidths=widths, repeatRows=1, hAlign="RIGHT")
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), self.brand),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.35, LINE),
            ("BOX", (0, 0), (-1, -1), 0.6, self.brand),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ROWBACKGROUNDS", (0, 1), (-1, len(body)), [colors.white, ZEBRA]),
        ]
        if totals:
            style += [
                ("BACKGROUND", (0, -1), (-1, -1), self.brand_light),
                ("LINEABOVE", (0, -1), (-1, -1), 0.8, self.brand),
            ]
        table.setStyle(TableStyle(style))
        self.story.append(table)
        return self

    def total_line(self, label, value, *, tone="brand"):
        """Headline amount. The label and the number are composed *before*
        shaping so bidi can place the Latin number on the correct side."""
        colour = {"brand": self.brand_dark, "success": SUCCESS, "danger": DANGER}[tone]
        style = ParagraphStyle("total_line", parent=STYLES["total"], textColor=colour)
        self.story.append(Paragraph(ar(f"{label}: {fmt_money(value)} ج.م"), style))
        return self

    def side_by_side(self, image, lines, *, image_size=22, image_first=False):
        """An image beside a stack of text — badges, cards, headed blocks.

        ``lines``: [(text, style_name)]
        """
        from reportlab.platypus import Image

        text_block = [Paragraph(ar(text), STYLES[style]) for text, style in lines]
        picture = Image(io.BytesIO(image), width=image_size * mm, height=image_size * mm)

        cells = [picture, text_block] if image_first else [text_block, picture]
        widths = (
            [image_size * mm + 4, self.content_width - image_size * mm - 4]
            if image_first
            else [self.content_width - image_size * mm - 4, image_size * mm + 4]
        )
        table = Table([cells], colWidths=widths, hAlign="RIGHT")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        self.story.append(table)
        return self

    def image_grid(self, items, *, columns=3, image_size=32):
        """Grid of images with captions — the card sheet is built from this.

        ``items``: [{"image": png_bytes, "caption": str, "sub": str}]
        """
        from reportlab.platypus import Image

        cell_width = self.content_width / columns
        cells = []
        for item in items:
            block = [
                Image(io.BytesIO(item["image"]), width=image_size * mm, height=image_size * mm),
                Paragraph(ar(item.get("caption", "")), STYLES["cell_bold"]),
            ]
            if item.get("sub"):
                block.append(Paragraph(ar(item["sub"]), STYLES["small"]))
            cells.append(block)

        rows = [cells[i : i + columns] for i in range(0, len(cells), columns)]
        for row in rows:
            while len(row) < columns:
                row.append("")

        table = Table(rows, colWidths=[cell_width] * columns, hAlign="RIGHT")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("GRID", (0, 0), (-1, -1), 0.3, LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        self.story.append(table)
        return self

    def signature_row(self, left, right):
        data = [[Paragraph(ar(left), STYLES["small"]), Paragraph(ar(right), STYLES["small"])]]
        table = Table(data, colWidths=[self.content_width / 2] * 2, hAlign="RIGHT")
        table.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 18)]))
        self.story.append(KeepTogether(table))
        return self

    # ------------------------------------------------------------- output --
    @property
    def content_width(self) -> float:
        return self.pagesize[0] - 2 * self.margin

    def _page_furniture(self, canvas, doc):
        """Header band and footer drawn on every page."""
        canvas.saveState()
        width, height = self.pagesize

        if not self.compact:
            canvas.setFillColor(self.brand_dark)
            canvas.rect(0, height - 20 * mm, width, 20 * mm, stroke=0, fill=1)
            canvas.setFillColor(colors.white)
            canvas.setFont(FONT_BOLD, 13)
            canvas.drawRightString(width - self.margin, height - 11 * mm, ar(self.meta.center_name))
            canvas.setFont(FONT_REGULAR, 8)
            contact = " · ".join(x for x in (self.meta.center_phone, self.meta.center_address) if x)
            if contact:
                canvas.drawRightString(width - self.margin, height - 16 * mm, ar(contact))
            canvas.drawString(self.margin, height - 11 * mm, ar(self.meta.title))

        if self.chromeless:
            canvas.restoreState()
            return

        canvas.setFillColor(INK_SOFT)
        canvas.setFont(FONT_REGULAR, 7.5)
        stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
        canvas.drawRightString(
            width - self.margin,
            8 * mm,
            ar(
                f"طُبع في {stamp}"
                + (f" — {self.meta.generated_by}" if self.meta.generated_by else "")
            ),
        )
        canvas.drawString(self.margin, 8 * mm, ar(f"صفحة {doc.page}"))
        if self.meta.footer_note:
            canvas.drawCentredString(width / 2, 8 * mm, ar(self.meta.footer_note))
        canvas.setStrokeColor(LINE)
        canvas.line(self.margin, 11 * mm, width - self.margin, 11 * mm)
        canvas.restoreState()

    def _prelude(self):
        """Title block that sits above the content on page one."""
        if self.chromeless:
            return []

        title_style = ParagraphStyle("doc_title", parent=STYLES["title"], textColor=self.brand_dark)
        block = [Paragraph(ar(self.meta.title), title_style)]
        if self.compact and self.meta.center_name:
            block.insert(0, Paragraph(ar(self.meta.center_name), title_style))
        if self.meta.subtitle:
            block.append(Paragraph(ar(self.meta.subtitle), STYLES["subtitle"]))
        if self.meta.filters:
            text = " · ".join(f"{label}: {value}" for label, value in self.meta.filters)
            block.append(Paragraph(ar(text), STYLES["small"]))
        block.append(Spacer(1, 8))
        return block

    def build(self) -> bytes:
        top_margin = self.margin + (0 if self.compact else 14 * mm)
        template = BaseDocTemplate(
            self._buffer,
            pagesize=self.pagesize,
            leftMargin=self.margin,
            rightMargin=self.margin,
            topMargin=top_margin,
            bottomMargin=self.margin + 6 * mm,
            title=self.meta.title,
            author=self.meta.center_name,
        )
        frame = Frame(
            template.leftMargin,
            template.bottomMargin,
            template.width,
            template.height,
            id="body",
        )
        template.addPageTemplates(
            [PageTemplate(id="main", frames=[frame], onPage=self._page_furniture)]
        )
        template.build(self._prelude() + self.story)
        return self._buffer.getvalue()

    def response(self, filename: str, *, inline=False) -> HttpResponse:
        """Always a download by default — no browser print dialogs."""
        pdf = self.build()
        response = HttpResponse(pdf, content_type="application/pdf")
        disposition = "inline" if inline else "attachment"
        response["Content-Disposition"] = f'{disposition}; filename="{filename}.pdf"'
        response["Content-Length"] = str(len(pdf))
        response["X-Content-Type-Options"] = "nosniff"
        return response


def document_meta(
    request=None, *, title, subtitle="", filters=None, footer_note=""
) -> DocumentMeta:
    """Assemble the header/footer identity from the settings registry."""
    from .registry import settings_registry

    return DocumentMeta(
        title=title,
        subtitle=subtitle,
        center_name=settings_registry.get("center.name"),
        center_phone=settings_registry.get("center.phone"),
        center_address=settings_registry.get("center.address"),
        generated_by=(
            request.user.display_name if request and request.user.is_authenticated else ""
        ),
        footer_note=footer_note,
        filters=filters or [],
    )
