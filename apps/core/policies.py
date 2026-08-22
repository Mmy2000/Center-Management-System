"""Policy defaults for the whole system (docs/03 §D.7).

Every configurable rule the brief demands lives here as a :class:`PolicySpec`.
The database stores *overrides only* — a missing row is not an error, and an
unknown key is always an error (typo protection).
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

INT = "INT"
BOOL = "BOOL"
STR = "STR"
DECIMAL = "DECIMAL"
CHOICE = "CHOICE"

# Policy groups (used to lay the settings screen out)
G_ATTENDANCE = "attendance"
G_PAYMENTS = "payments"
G_BILLING = "billing"
G_CARDS = "cards"
G_CENTER = "center"
G_UI = "ui"

# Palette presets — the CSS side lives in static/css/themes.css.
THEMES = ("teal", "indigo", "violet", "emerald", "sunset", "slate", "custom")


@dataclass(frozen=True)
class PolicySpec:
    key: str
    default: Any
    value_type: str
    group: str
    label: str
    help_text: str = ""
    choices: tuple = ()
    min_value: int | None = None
    max_value: int | None = None
    pattern: str = ""
    is_editable: bool = True

    def coerce(self, raw: Any) -> Any:
        """Convert a raw (form/JSON) value into the policy's Python type."""
        if self.value_type == BOOL:
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in {"1", "true", "yes", "on", "نعم"}
        if self.value_type == INT:
            try:
                return int(raw)
            except (TypeError, ValueError) as exc:
                raise ValidationError(_("قيمة رقمية غير صحيحة")) from exc
        if self.value_type == DECIMAL:
            try:
                return Decimal(str(raw))
            except (TypeError, InvalidOperation) as exc:
                raise ValidationError(_("قيمة عشرية غير صحيحة")) from exc
        return str(raw)

    def validate(self, value: Any) -> Any:
        if self.pattern and not re.fullmatch(self.pattern, str(value)):
            raise ValidationError(_("صيغة غير صحيحة"))
        if self.value_type == CHOICE and value not in self.choices:
            raise ValidationError(
                _("قيمة غير مسموح بها: %(v)s") % {"v": value},
            )
        if self.value_type in (INT, DECIMAL):
            if self.min_value is not None and value < self.min_value:
                raise ValidationError(
                    _("القيمة أقل من الحد الأدنى (%(m)s)") % {"m": self.min_value}
                )
            if self.max_value is not None and value > self.max_value:
                raise ValidationError(
                    _("القيمة أكبر من الحد الأقصى (%(m)s)") % {"m": self.max_value}
                )
        return value

    def to_json(self, value: Any) -> Any:
        """JSONField-safe representation."""
        if self.value_type == DECIMAL:
            return str(value)
        return value


BLOCK = "BLOCK"
WARN = "WARN"
REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
ALLOW = "ALLOW"
ALLOW_EXCEPTIONAL = "ALLOW_EXCEPTIONAL"


def _spec(*args, **kwargs) -> PolicySpec:
    return PolicySpec(*args, **kwargs)


SPECS: tuple[PolicySpec, ...] = (
    # ----------------------------- attendance ----------------------------- #
    _spec(
        "attendance.window_open_before_minutes",
        30,
        INT,
        G_ATTENDANCE,
        _("فتح التسجيل قبل بداية الحصة (دقائق)"),
        min_value=0,
        max_value=600,
    ),
    _spec(
        "attendance.window_close_after_minutes",
        150,
        INT,
        G_ATTENDANCE,
        _("إغلاق التسجيل بعد بداية الحصة (دقائق)"),
        min_value=1,
        max_value=1440,
    ),
    _spec(
        "attendance.outside_window_policy",
        REQUIRE_APPROVAL,
        CHOICE,
        G_ATTENDANCE,
        _("سلوك المسح خارج وقت التسجيل"),
        choices=(BLOCK, REQUIRE_APPROVAL, ALLOW_EXCEPTIONAL),
    ),
    _spec(
        "attendance.late_after_minutes",
        15,
        INT,
        G_ATTENDANCE,
        _("اعتبار الطالب متأخرًا بعد (دقائق)"),
        min_value=0,
        max_value=240,
        help_text=_("يمكن لكل مجموعة تجاوز هذه القيمة"),
    ),
    _spec(
        "attendance.duplicate_window_seconds",
        20,
        INT,
        G_ATTENDANCE,
        _("منع المسح المكرر خلال (ثواني)"),
        min_value=0,
        max_value=600,
    ),
    _spec(
        "attendance.min_checkout_gap_minutes",
        10,
        INT,
        G_ATTENDANCE,
        _("أقل مدة قبل اعتبار المسح انصرافًا (دقائق)"),
        min_value=0,
        max_value=600,
    ),
    _spec(
        "attendance.partial_min_duration_minutes",
        30,
        INT,
        G_ATTENDANCE,
        _("أقل مدة حضور كامل (دقائق)"),
        min_value=0,
        max_value=600,
    ),
    _spec(
        "attendance.auto_checkout_at_lesson_end",
        True,
        BOOL,
        G_ATTENDANCE,
        _("تسجيل الانصراف تلقائيًا عند إنهاء الحصة"),
    ),
    _spec(
        "attendance.materialize_absences",
        True,
        BOOL,
        G_ATTENDANCE,
        _("تسجيل الغياب تلقائيًا عند إنهاء الحصة"),
    ),
    _spec(
        "attendance.not_assigned_policy",
        REQUIRE_APPROVAL,
        CHOICE,
        G_ATTENDANCE,
        _("طالب غير مقيد بالمادة"),
        choices=(BLOCK, WARN, REQUIRE_APPROVAL),
    ),
    _spec(
        "attendance.different_grade_policy",
        REQUIRE_APPROVAL,
        CHOICE,
        G_ATTENDANCE,
        _("طالب من صف/مادة مختلفة"),
        choices=(BLOCK, REQUIRE_APPROVAL),
    ),
    _spec(
        "attendance.alternative_group_policy",
        ALLOW,
        CHOICE,
        G_ATTENDANCE,
        _("الحضور في مجموعة بديلة"),
        choices=(ALLOW, WARN, REQUIRE_APPROVAL, BLOCK),
    ),
    # ------------------------------ payments ------------------------------ #
    _spec(
        "payments.enforce_on_attendance",
        False,
        BOOL,
        G_PAYMENTS,
        _("ربط الحضور بالسداد"),
        help_text=_("عند الإيقاف لا يتم تنفيذ أي استعلام مالي أثناء المسح"),
    ),
    _spec(
        "payments.allow_unpaid_attendance",
        True,
        BOOL,
        G_PAYMENTS,
        _("السماح بحضور غير المسدد"),
    ),
    _spec(
        "payments.allow_partial_attendance",
        True,
        BOOL,
        G_PAYMENTS,
        _("السماح بحضور المسدد جزئيًا"),
    ),
    _spec(
        "payments.unpaid_block_action",
        REQUIRE_APPROVAL,
        CHOICE,
        G_PAYMENTS,
        _("الإجراء عند المنع لعدم السداد"),
        choices=(BLOCK, REQUIRE_APPROVAL),
    ),
    _spec(
        "payments.grace_days",
        10,
        INT,
        G_PAYMENTS,
        _("مهلة السداد (أيام من بداية الشهر)"),
        min_value=0,
        max_value=31,
    ),
    _spec(
        "payments.allow_overpayment",
        False,
        BOOL,
        G_PAYMENTS,
        _("السماح بالسداد الزائد"),
    ),
    # ------------------------------ billing ------------------------------- #
    _spec(
        "billing.due_day_of_month",
        5,
        INT,
        G_BILLING,
        _("يوم استحقاق الرسوم من الشهر"),
        min_value=1,
        max_value=28,
    ),
    _spec(
        "billing.auto_generate_day",
        1,
        INT,
        G_BILLING,
        _("يوم إصدار رسوم الشهر تلقائيًا"),
        min_value=1,
        max_value=28,
    ),
    # ------------------------------- cards -------------------------------- #
    _spec(
        "cards.token_prefix",
        "CMS1",
        STR,
        G_CARDS,
        _("بادئة رمز البطاقة"),
    ),
    _spec(
        "cards.accept_unprefixed_tokens",
        True,
        BOOL,
        G_CARDS,
        _("قبول بطاقات بدون بادئة (بطاقات مطبوعة مسبقًا)"),
    ),
    # ------------------------------- center ------------------------------- #
    _spec(
        "center.name",
        "سنتر التفوق",
        STR,
        G_CENTER,
        _("اسم السنتر"),
        help_text=_("يظهر أعلى الشاشة وفي رأس كل مستند PDF"),
    ),
    _spec("center.phone", "", STR, G_CENTER, _("هاتف السنتر")),
    _spec("center.address", "", STR, G_CENTER, _("عنوان السنتر")),
    _spec(
        "center.receipt_footer",
        "شكرًا لثقتكم بنا",
        STR,
        G_CENTER,
        _("تذييل الإيصال"),
    ),
    # ----------------------------- appearance ----------------------------- #
    _spec(
        "ui.theme",
        "teal",
        CHOICE,
        G_UI,
        _("لوحة الألوان"),
        choices=THEMES,
        help_text=_("اللون الافتراضي لكل المستخدمين — ويمكن لكل مستخدم تغييره لنفسه"),
    ),
    _spec(
        "ui.accent",
        "#1f6f8b",
        STR,
        G_UI,
        _("لون مخصص (Hex)"),
        pattern=r"#[0-9a-fA-F]{6}",
        help_text=_("يُستخدم عند اختيار «مخصص» — ويظهر أيضًا في رأس مستندات PDF"),
    ),
    _spec(
        "ui.mode",
        "light",
        CHOICE,
        G_UI,
        _("الوضع"),
        choices=("light", "dark", "auto"),
    ),
    _spec(
        "ui.density",
        "comfortable",
        CHOICE,
        G_UI,
        _("كثافة العرض"),
        choices=("comfortable", "compact"),
    ),
)

SPECS_BY_KEY: dict[str, PolicySpec] = {s.key: s for s in SPECS}
DEFAULTS: dict[str, Any] = {s.key: s.default for s in SPECS}
GROUPS: tuple[str, ...] = (G_CENTER, G_UI, G_ATTENDANCE, G_PAYMENTS, G_BILLING, G_CARDS)
GROUP_LABELS = {
    G_ATTENDANCE: _("الحضور"),
    G_PAYMENTS: _("المدفوعات"),
    G_BILLING: _("الفوترة"),
    G_CARDS: _("البطاقات"),
    G_CENTER: _("بيانات السنتر"),
    G_UI: _("المظهر"),
}


def spec_for(key: str) -> PolicySpec:
    try:
        return SPECS_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"Unknown policy key: {key!r}") from exc


def specs_for_group(group: str) -> list[PolicySpec]:
    return [s for s in SPECS if s.group == group]


__all__ = [
    "ALLOW",
    "ALLOW_EXCEPTIONAL",
    "BLOCK",
    "DEFAULTS",
    "GROUPS",
    "GROUP_LABELS",
    "REQUIRE_APPROVAL",
    "SPECS",
    "SPECS_BY_KEY",
    "WARN",
    "PolicySpec",
    "spec_for",
    "specs_for_group",
]
