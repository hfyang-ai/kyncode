from dedupe import dedupe


def test_small():
    assert dedupe([1, 2, 2, 3, 1]) == [1, 2, 3]


def test_order():
    assert dedupe(['b', 'a', 'b', 'c', 'a']) == ['b', 'a', 'c']


def test_large():
    n = 30000
    items = [i % 1000 for i in range(n)]
    assert dedupe(items) == list(range(1000))
