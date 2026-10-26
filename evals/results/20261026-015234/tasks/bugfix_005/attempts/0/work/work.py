def _to_number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        return float(text)


def parse_and_add(a, b):
    return _to_number(a) + _to_number(b)
