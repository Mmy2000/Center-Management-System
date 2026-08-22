"""Money formatting for templates.

Django's ``floatformat`` follows the active locale, which under ``ar`` renders
"200,00". Egyptian receipts and financial screens use Latin digits with a comma
thousands separator and a dot decimal — the convention documented in
docs/README, and the one staff compare against the cash drawer.
"""

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

CENTS = Decimal("0.01")


@register.filter(is_safe=True)
def money(value, with_currency=False):
    try:
        amount = Decimal(str(value or 0)).quantize(CENTS)
    except (InvalidOperation, TypeError, ValueError):
        return value
    text = f"{amount:,.2f}"
    return f"{text} ج.م" if with_currency else text
