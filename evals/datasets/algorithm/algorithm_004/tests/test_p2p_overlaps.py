from intervals import overlaps


def test_overlaps():
    assert overlaps([1, 3], [2, 4]) is True
    assert overlaps([1, 2], [3, 4]) is False
