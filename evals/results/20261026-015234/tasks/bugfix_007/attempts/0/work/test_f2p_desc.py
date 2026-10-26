from work import sorted_desc


def test_sorted_desc():
    assert sorted_desc([1, 3, 2]) == [3, 2, 1]
