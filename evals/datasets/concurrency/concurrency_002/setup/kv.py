def keys_of(mapping):
    return list(mapping.keys())


class ThreadSafeKV:
    def __init__(self):
        raise NotImplementedError

    def get(self, key, default=None):
        raise NotImplementedError

    def set(self, key, value):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError
