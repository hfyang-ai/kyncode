from work import is_adult


def test_is_adult_at_18():
    assert is_adult(18) is True
