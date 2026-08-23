"""The switchable-feature catalogue (docs/10 §N.8, TASK-090).

Deliberately the same shape as :mod:`apps.core.policies`: the catalogue is
*code*, the database holds overrides only, and an unknown key is always an error
rather than a silent ``False``. One idiom for both kinds of configuration means
the console screens and the tests look the same for both.

A feature answers "what did this center buy". A permission answers "what may
this user do". **Both** must pass; they are never conflated, and the permission
matrix in :mod:`apps.accounts.permissions` stays identical for every center.
"""

from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

from .exceptions import UnknownFeature

# Feature groups (used to lay the console's toggle screen out).
G_CORE = "core"
G_ATTENDANCE = "attendance"
G_CARDS = "cards"
G_PAYMENTS = "payments"
G_REPORTS = "reports"
G_UI = "ui"
G_ADMIN = "admin"

GROUP_LABELS = {
    G_CORE: _("الأساسيات"),
    G_ATTENDANCE: _("الحضور"),
    G_CARDS: _("البطاقات"),
    G_PAYMENTS: _("المدفوعات"),
    G_REPORTS: _("التقارير"),
    G_UI: _("الواجهة"),
    G_ADMIN: _("الإدارة"),
}


@dataclass(frozen=True)
class FeatureSpec:
    key: str
    label: str
    group: str
    default: bool
    help_text: str = ""
    #: Keys that must all be enabled for this one to resolve True. Applied
    #: transitively, so switching ``payments`` off also switches off every
    #: report that reads money.
    depends_on: tuple[str, ...] = field(default=())
    #: A feature the product cannot function without. Always on; the console
    #: renders it disabled rather than hiding it, so an operator can see that
    #: the switch exists and why it cannot move.
    is_core: bool = False
    #: A *way of recording attendance* — QR today, fingerprint next. These are
    #: switchable individually, but a center must keep at least one: a center
    #: that can take no attendance at all is not a running center, and the
    #: console refuses the toggle that would leave it there (see
    #: ``resolver.remaining_methods``).
    is_method: bool = False


def _f(*args, **kwargs) -> FeatureSpec:
    return FeatureSpec(*args, **kwargs)


FEATURES: tuple[FeatureSpec, ...] = (
    # -------------------------------- core -------------------------------- #
    _f(
        "students",
        _("إدارة الطلاب"),
        G_CORE,
        True,
        help_text=_("تسجيل الطلاب وبياناتهم — لا يمكن تعطيلها."),
        is_core=True,
    ),
    _f(
        "academics",
        _("الهيكل التعليمي"),
        G_CORE,
        True,
        help_text=_("المراحل والصفوف والمواد والمجموعات — لا يمكن تعطيلها."),
        is_core=True,
    ),
    _f(
        "attendance.scan",
        _("تسجيل الحضور"),
        G_CORE,
        True,
        help_text=_("تسجيل حضور الطلاب — لا يمكن تعطيله. الطريقة نفسها قابلة للاختيار."),
        is_core=True,
    ),
    # ------------------------ attendance: methods ------------------------- #
    # How attendance is taken. Each is switchable on its own, but the console
    # will not let the last one be switched off.
    _f(
        "attendance.qr",
        _("الحضور بالبطاقة (QR)"),
        G_ATTENDANCE,
        True,
        help_text=_("الطريقة الافتراضية: مسح بطاقة الطالب بقارئ باركود أو بالكاميرا."),
        is_method=True,
    ),
    _f(
        "attendance.fingerprint",
        _("الحضور ببصمة الإصبع"),
        G_ATTENDANCE,
        False,
        help_text=_("جهاز بصمة بدلًا من البطاقة. غير مُنفّذ بعد — الحقل محجوز."),
        is_method=True,
    ),
    _f(
        "attendance.manual",
        _("التسجيل اليدوي"),
        G_ATTENDANCE,
        True,
        help_text=_("تسجيل الحضور من كشف الحصة دون أي جهاز."),
        is_method=True,
    ),
    # ------------------------ attendance: options ------------------------- #
    _f(
        "attendance.camera_scanner",
        _("المسح بكاميرا الهاتف"),
        G_ATTENDANCE,
        True,
        help_text=_("البديل عن قارئ الباركود عبر كاميرا الجهاز."),
        depends_on=("attendance.qr",),
    ),
    _f(
        "attendance.exceptional",
        _("الحضور الاستثنائي"),
        G_ATTENDANCE,
        True,
        help_text=_("تسجيل حضور خارج المجموعة أو خارج النافذة الزمنية باعتماد."),
    ),
    _f(
        "attendance.offline_queue",
        _("طابور المسح دون اتصال"),
        G_ATTENDANCE,
        False,
        help_text=_("تخزين عمليات المسح محليًا عند انقطاع الإنترنت ورفعها لاحقًا."),
        depends_on=("attendance.qr",),
    ),
    # -------------------------------- cards ------------------------------- #
    _f(
        "cards",
        _("إدارة البطاقات"),
        G_CARDS,
        True,
        help_text=_("ربط البطاقات المطبوعة بالطلاب وإدارة الفقد والاستبدال."),
    ),
    _f(
        "cards.bulk_import",
        _("استيراد دفعات البطاقات"),
        G_CARDS,
        True,
        depends_on=("cards",),
    ),
    # ------------------------------ payments ------------------------------ #
    _f(
        "payments",
        _("المدفوعات"),
        G_PAYMENTS,
        True,
        help_text=_("الرسوم الشهرية والتحصيل. تعطيلها يخفي الشاشات ولا يحذف أي بيانات."),
    ),
    _f("payments.refunds", _("الاستردادات"), G_PAYMENTS, True, depends_on=("payments",)),
    _f("payments.waivers", _("الإعفاءات"), G_PAYMENTS, True, depends_on=("payments",)),
    _f(
        "payments.receipt_pdf",
        _("إيصالات PDF"),
        G_PAYMENTS,
        True,
        depends_on=("payments",),
    ),
    _f(
        "payments.enforce_on_attendance",
        _("ربط الحضور بالسداد"),
        G_PAYMENTS,
        False,
        help_text=_("إتاحة سياسة منع أو تنبيه الطالب المتأخر عند المسح."),
        depends_on=("payments",),
    ),
    # ------------------------------- reports ------------------------------ #
    _f("reports.operational", _("التقارير التشغيلية"), G_REPORTS, True),
    _f(
        "reports.financial",
        _("التقارير المالية"),
        G_REPORTS,
        True,
        depends_on=("payments",),
    ),
    _f("reports.export_excel", _("تصدير Excel"), G_REPORTS, True),
    _f("reports.export_pdf", _("تصدير PDF"), G_REPORTS, True),
    # --------------------------------- ui --------------------------------- #
    _f("ui.theme_picker", _("اختيار الألوان والمظهر"), G_UI, True),
    _f(
        "i18n.english",
        _("الواجهة الإنجليزية"),
        G_UI,
        True,
        help_text=_("تعطيلها يخفي مبدّل اللغة ويثبّت الواجهة على العربية."),
    ),
    # -------------------------------- admin ------------------------------- #
    _f("audit.viewer", _("سجل التدقيق"), G_ADMIN, True),
    _f(
        "settings.editor",
        _("تعديل الإعدادات"),
        G_ADMIN,
        True,
        help_text=_("تعطيلها يجمّد السياسات على ما تم ضبطه عند التجهيز."),
    ),
    _f(
        "users.management",
        _("إدارة المستخدمين"),
        G_ADMIN,
        True,
        help_text=_("تعطيلها يعني أن المشغّل هو من ينشئ حسابات موظفي السنتر."),
    ),
)

_BY_KEY: dict[str, FeatureSpec] = {spec.key: spec for spec in FEATURES}

FEATURE_KEYS: frozenset[str] = frozenset(_BY_KEY)

#: Always-on keys, precomputed — the resolver consults this on every request.
CORE_KEYS: frozenset[str] = frozenset(s.key for s in FEATURES if s.is_core)

#: The ways attendance can be recorded. A center must keep at least one.
METHOD_KEYS: frozenset[str] = frozenset(s.key for s in FEATURES if s.is_method)

#: Methods that are actually built. ``attendance.fingerprint`` is in the
#: catalogue so the console can show what is coming and a plan can already be
#: priced for it, but switching it on today would promise a screen that does
#: not exist — so it does not count towards "at least one method".
IMPLEMENTED_METHOD_KEYS: frozenset[str] = frozenset({"attendance.qr", "attendance.manual"})

#: Keys that are on unless something says otherwise.
DEFAULT_ON_KEYS: frozenset[str] = frozenset(s.key for s in FEATURES if s.default)


def spec_for(key: str) -> FeatureSpec:
    """Return the spec for ``key``; a typo raises rather than resolving False.

    A silent ``False`` here would disable a feature nobody meant to disable, and
    the symptom (a missing menu entry) is a long way from the cause (a typo in
    a decorator).
    """
    try:
        return _BY_KEY[key]
    except KeyError:
        raise UnknownFeature(key) from None


def specs_for_group(group: str) -> list[FeatureSpec]:
    return [spec for spec in FEATURES if spec.group == group]


def grouped_specs() -> list[tuple[str, str, list[FeatureSpec]]]:
    """``[(group, label, specs), …]`` in catalogue order — for the console UI."""
    seen: list[str] = []
    for spec in FEATURES:
        if spec.group not in seen:
            seen.append(spec.group)
    return [(g, GROUP_LABELS.get(g, g), specs_for_group(g)) for g in seen]


def dependency_closure(key: str) -> frozenset[str]:
    """Every key ``key`` transitively depends on (excluding itself)."""
    out: set[str] = set()
    stack = list(spec_for(key).depends_on)
    while stack:
        current = stack.pop()
        if current in out:
            continue
        out.add(current)
        stack.extend(spec_for(current).depends_on)
    return frozenset(out)
