def parse_int(s):
    try:
        return int(s)
    except ValueError:
        return 0
