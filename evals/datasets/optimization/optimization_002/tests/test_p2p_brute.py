from prefixsum import brute_sum


def test_brute():
    assert brute_sum([1, 2, 3], 0, 1) == 3
