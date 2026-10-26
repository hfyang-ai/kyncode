from sum import total


def test_total():
    assert total([1, 2, 3]) == 6


def test_empty():
    assert total([]) == 0
