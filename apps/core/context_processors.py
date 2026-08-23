"""Template context shared by every page."""

from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from apps.tenancy.resolver import enabled_features

from .registry import settings_registry

#: How early a trial starts warning. A week is enough to arrange payment and
#: short enough that the banner does not become wallpaper.
TRIAL_WARNING_DAYS = 7


def features(request):
    """``FEATURES`` — what this center bought, as a frozenset of keys.

    Lets the nav read ``{% if perms.payments.view_monthlycharge and "payments" in FEATURES %}``
    with no extra tag. Costs nothing: the resolver caches one frozenset per
    tenant, so this is a dict lookup, not a query (docs/10 §N.8).
    """
    return {"FEATURES": enabled_features(getattr(request, "tenant", None))}


def subscription_notice(request):
    """A banner while a trial is running out or an invoice is late (TASK-110).

    Deliberately a *notice*, not a block: PAST_DUE centers keep working (see
    ``Tenant.is_operational``). The gate page only appears once the grace period
    has expired and the console has actually suspended them.
    """
    tenant = getattr(request, "tenant", None)
    if tenant is None:
        return {"subscription_notice": None}

    from apps.tenancy.constants import TenantStatus

    days = tenant.days_until_expiry()

    if tenant.status == TenantStatus.PAST_DUE:
        return {
            "subscription_notice": {
                "level": "warning",
                "message": _(
                    "انتهى الاشتراك. الخدمة مستمرة مؤقتًا — برجاء التواصل مع مشغّل النظام."
                ),
            }
        }

    if tenant.status == TenantStatus.TRIAL and days is not None and days <= TRIAL_WARNING_DAYS:
        if days <= 0:
            message = _("الفترة التجريبية تنتهي اليوم.")
        else:
            message = ngettext(
                "تبقّى يوم واحد على انتهاء الفترة التجريبية.",
                "تبقّى %(days)s أيام على انتهاء الفترة التجريبية.",
                days,
            ) % {"days": days}
        return {"subscription_notice": {"level": "info", "message": message}}

    return {"subscription_notice": None}


def branding(request):
    """Center identity and appearance defaults.

    The appearance values are only *defaults*: theme.js layers each user's own
    choice (localStorage) on top before the first paint.
    """
    return {
        "CENTER_NAME": settings_registry.get("center.name"),
        "CENTER_PHONE": settings_registry.get("center.phone"),
        "CENTER_ADDRESS": settings_registry.get("center.address"),
        "UI_THEME": settings_registry.get("ui.theme"),
        "UI_ACCENT": settings_registry.get("ui.accent"),
        "UI_MODE": settings_registry.get("ui.mode"),
        "UI_DENSITY": settings_registry.get("ui.density"),
    }
