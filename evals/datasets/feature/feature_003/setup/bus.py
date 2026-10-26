def normalize(event):
    return event.strip().lower()


class EventBus:
    def __init__(self):
        raise NotImplementedError

    def on(self, event, handler):
        raise NotImplementedError

    def once(self, event, handler):
        raise NotImplementedError

    def emit(self, event, *args):
        raise NotImplementedError
