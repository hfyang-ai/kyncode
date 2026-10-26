from prefixsum import PrefixSum


def test_basic():
    ps = PrefixSum([1, 2, 3, 4, 5])
    assert ps.range_sum(1, 3) == 9
    assert ps.range_sum(0, 4) == 15


def test_large_many_queries():
    n = 30000
    nums = list(range(1, n + 1))
    ps = PrefixSum(nums)
    for _ in range(10000):
        assert ps.range_sum(0, n - 1) == n * (n + 1) // 2
