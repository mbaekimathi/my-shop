from django import template

from shops.services import HIDDEN_STOCK_LABEL, item_stock_is_hidden, stock_hide_state

register = template.Library()


@register.simple_tag
def stock_qty(value, item_id=None, category=""):
    """Show the on-hand number, or Hidden when this item is covered by the setting."""
    if item_stock_is_hidden(item_id, category):
        return HIDDEN_STOCK_LABEL
    if value is None or value == "":
        return 0
    return value


@register.simple_tag
def stock_level_total(value):
    """Hide a summed on-hand figure whenever any hide rule is active."""
    if stock_hide_state()["active"]:
        return HIDDEN_STOCK_LABEL
    try:
        from items.stock_take import open_stock_take_hide_sets

        if open_stock_take_hide_sets()["active"]:
            return HIDDEN_STOCK_LABEL
    except Exception:
        pass
    if value is None or value == "":
        return 0
    return value
