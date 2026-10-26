from work import square


def test_square_int():
    assert square(3) == 9


def test_square_is_int():
    assert isinstance(square(4), int)
