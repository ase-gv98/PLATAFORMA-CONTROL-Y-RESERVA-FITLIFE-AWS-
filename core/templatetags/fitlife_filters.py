from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def clp(value):
    try:
        amount = int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return value
    return f"${amount:,}".replace(",", ".")
