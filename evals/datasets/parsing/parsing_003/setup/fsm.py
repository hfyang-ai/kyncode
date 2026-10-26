def make_transitions():
    return {'idle': {'start': 'running'}, 'running': {'stop': 'idle', 'pause': 'paused'}, 'paused': {'resume': 'running'}}


class StateMachine:
    def __init__(self, initial, transitions):
        raise NotImplementedError

    def trigger(self, event):
        raise NotImplementedError

    @property
    def current(self):
        raise NotImplementedError
