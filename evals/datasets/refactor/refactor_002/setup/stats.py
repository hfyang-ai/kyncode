_total = 0
_count = 0


def add(n):
    global _total, _count
    _total += n
    _count += 1


def mean():
    return _total / _count if _count else 0


def count():
    return _count
