from inventory import get_price


def line_total(sku, qty):
    return get_price(sku) * qty


def format_currency(amount):
    return f'${amount:.2f}'
