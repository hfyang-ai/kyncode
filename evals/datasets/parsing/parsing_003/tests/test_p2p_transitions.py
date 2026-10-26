from fsm import make_transitions


def test_make():
    t = make_transitions()
    assert 'idle' in t and 'running' in t
