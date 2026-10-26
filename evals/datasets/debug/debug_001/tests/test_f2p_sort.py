from work import quicksort


def test_duplicates():
    assert quicksort([3, 1, 2, 1, 3]) == [1, 1, 2, 3, 3]


def test_sorted():
    assert quicksort([1, 2, 3, 4]) == [1, 2, 3, 4]


def test_reversed():
    assert quicksort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]


def test_single():
    assert quicksort([9]) == [9]
