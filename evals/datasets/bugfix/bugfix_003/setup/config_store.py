_cache = {}


def load(key):
    return {'name': 'svc', 'tags': ['a', 'b']}


def get(key):
    if key not in _cache:
        _cache[key] = load(key)
    return _cache[key]
