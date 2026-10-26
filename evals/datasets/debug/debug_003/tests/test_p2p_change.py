from work import make_change


def test_exact_fit():
    assert make_change([1, 5, 10], 10) == [10]
