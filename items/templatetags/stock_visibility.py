from django import template

from shops.services import HIDDEN_STOCK_LABEL, item_stock_is_hidden, stock_hide_state

register = template.Library()


@register.filter
def index_at(sequence, position):
    """Safe list/tuple lookup for template loops that need a parallel shop column."""
    try:
        return sequence[int(position)]
    except (TypeError, ValueError, IndexError, KeyError):
        return None


@register.simple_tag
def stock_qty(value, item_id=None, category="", shop_id=None):
    """Show the on-hand number, or Hidden when this item is covered by the setting."""
    if item_stock_is_hidden(item_id, category, shop_id=shop_id):
        return HIDDEN_STOCK_LABEL
    if value is None or value == "":
        return 0
    return value


@register.simple_tag
def stock_level_total(value):
    """Hide a summed on-hand figure for company-wide hide settings only.

    Stock-take masking is shop+item specific, so aggregates stay visible unless
    the company hide-stock setting is active.
    """
    if stock_hide_state()["active"]:
        return HIDDEN_STOCK_LABEL
    if value is None or value == "":
        return 0
    return value
