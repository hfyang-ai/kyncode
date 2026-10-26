from work import average


def test_average_fixed():
    assert average([2, 4, 6]) == 4


def test_average_other():
    assert average([1, 2, 3]) == 2
