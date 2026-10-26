from work import divide


def test_divide_by_zero():
    assert divide(10, 0) == 0
