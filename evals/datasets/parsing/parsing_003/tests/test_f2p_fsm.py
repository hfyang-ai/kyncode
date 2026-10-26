import pytest
from fsm import StateMachine, make_transitions


def test_transitions():
    sm = StateMachine('idle', make_transitions())
    assert sm.current == 'idle'
    assert sm.trigger('start') == 'running'
    assert sm.trigger('pause') == 'paused'
    assert sm.trigger('resume') == 'running'
    assert sm.trigger('stop') == 'idle'


def test_illegal():
    sm = StateMachine('idle', make_transitions())
    with pytest.raises(ValueError):
        sm.trigger('stop')
