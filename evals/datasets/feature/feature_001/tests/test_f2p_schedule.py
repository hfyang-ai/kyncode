import pytest
from scheduler import schedule


def test_linear():
    tasks = {'a': [], 'b': ['a'], 'c': ['b']}
    order = schedule(tasks)
    assert order.index('a') < order.index('b') < order.index('c')


def test_branch():
    tasks = {'a': [], 'b': ['a'], 'c': ['a'], 'd': ['b', 'c']}
    order = schedule(tasks)
    assert order.index('a') < order.index('d')
    assert order.index('b') < order.index('d')
    assert order.index('c') < order.index('d')


def test_cycle():
    tasks = {'a': ['b'], 'b': ['a']}
    with pytest.raises(ValueError):
        schedule(tasks)
