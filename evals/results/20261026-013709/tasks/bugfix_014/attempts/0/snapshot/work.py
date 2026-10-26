def clamp(value, lo, hi):
    if value < lo:
        return lo
    if value > hi:
        return lo
    return value
