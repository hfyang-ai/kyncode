from stats import Stats


def test_isolated_instances():
    a = Stats()
    b = Stats()
    a.add(10)
    a.add(20)
    b.add(100)
    assert a.mean() == 15
    assert b.mean() == 100


def test_count():
    s = Stats()
    s.add(1)
    s.add(2)
    assert s.count() == 2


def test_empty_mean():
    assert Stats().mean() == 0
