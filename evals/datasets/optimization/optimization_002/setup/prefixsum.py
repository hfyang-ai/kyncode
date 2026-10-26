def brute_sum(nums, i, j):
    return sum(nums[i:j + 1])


class PrefixSum:
    def __init__(self, nums):
        raise NotImplementedError

    def range_sum(self, i, j):
        raise NotImplementedError
