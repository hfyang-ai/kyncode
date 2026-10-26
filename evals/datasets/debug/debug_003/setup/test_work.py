from work import make_change


def test_min_coins():
    assert len(make_change([1, 5, 7], 10)) == 2
