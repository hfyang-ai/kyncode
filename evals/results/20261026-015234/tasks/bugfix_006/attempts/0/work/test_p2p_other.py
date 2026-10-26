from work import is_adult


def test_20_is_adult():
    assert is_adult(20) is True


def test_17_not_adult():
    assert is_adult(17) is False
