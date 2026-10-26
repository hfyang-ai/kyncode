from work import quicksort


def test_duplicates():
    assert quicksort([3, 1, 2, 1, 3]) == [1, 1, 2, 3, 3]
