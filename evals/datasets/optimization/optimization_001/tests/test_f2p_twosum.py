from twosum import two_sum


def test_small():
    i, j = two_sum([2, 7, 11, 15], 9)
    assert sorted([i, j]) == [0, 1]


def test_large():
    n = 30000
    nums = list(range(n))
    i, j = two_sum(nums, n - 2 + n - 1)
    assert sorted([i, j]) == [n - 2, n - 1]


def test_duplicates():
    i, j = two_sum([3, 3], 6)
    assert sorted([i, j]) == [0, 1]
