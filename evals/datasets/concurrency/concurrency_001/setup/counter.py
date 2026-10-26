def describe():
    return 'thread-safe counter'


class ThreadSafeCounter:
    def __init__(self):
        raise NotImplementedError

    def increment(self):
        raise NotImplementedError

    def value(self):
        raise NotImplementedError
