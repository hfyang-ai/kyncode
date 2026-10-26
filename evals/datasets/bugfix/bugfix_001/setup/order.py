from pricing import line_total


def order_total(items):
    total = 0
    for sku, qty in items:
        total += line_total(sku, qty)
    return total
