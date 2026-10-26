Sentinel = object()


class BoundedQueue:
    def __init__(self, maxsize=100):
        raise NotImplementedError

    def put(self, item):
        raise NotImplementedError

    def get(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def __iter__(self):
        raise NotImplementedError
