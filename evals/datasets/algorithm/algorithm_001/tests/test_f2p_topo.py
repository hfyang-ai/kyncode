import pytest
from toposort import topological_sort


def test_chain():
    order = topological_sort([1, 2, 3], [(1, 2), (2, 3)])
    assert order.index(1) < order.index(2) < order.index(3)


def test_diamond():
    order = topological_sort(['a', 'b', 'c', 'd'], [('a', 'b'), ('a', 'c'), ('b', 'd'), ('c', 'd')])
    assert order.index('a') < order.index('b')
    assert order.index('a') < order.index('c')
    assert order.index('b') < order.index('d')
    assert order.index('c') < order.index('d')


def test_cycle():
    with pytest.raises(ValueError):
        topological_sort([1, 2], [(1, 2), (2, 1)])
