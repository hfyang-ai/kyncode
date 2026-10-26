from work import square


def test_square_type():
    assert square(3) == 9
    assert isinstance(square(3), int)
