from work import make_change


def test_min_coins():
    assert len(make_change([1, 5, 7], 10)) == 2


def test_another_min():
    assert len(make_change([1, 3, 4], 6)) == 2


def test_sum_correct():
    coins = make_change([1, 2, 5], 11)
    assert sum(coins) == 11
    assert len(coins) == 3


def test_single_coin():
    assert make_change([1], 3) == [1, 1, 1]
