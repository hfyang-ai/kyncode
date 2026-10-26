def days_in_month(year, month):
    if month in (4, 6, 9, 11):
        return 30
    if month == 2:
        return 29
    return 31


def days_between(y1, m1, d1, y2, m2, d2):
    from datetime import date
    return (date(y2, m2, d2) - date(y1, m1, d1)).days
