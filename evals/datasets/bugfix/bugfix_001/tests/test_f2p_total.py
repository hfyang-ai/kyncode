from order import order_total


def test_two_items():
    assert order_total([('a', 2), ('b', 1)]) == 60


def test_single():
    assert order_total([('a', 1)]) == 20
