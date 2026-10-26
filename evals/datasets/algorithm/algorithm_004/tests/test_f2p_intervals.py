from intervals import merge, insert


def test_merge_overlapping():
    assert merge([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]


def test_merge_touching():
    assert merge([[1, 4], [4, 5]]) == [[1, 5]]


def test_insert():
    assert insert([[1, 3], [6, 9]], [2, 5]) == [[1, 5], [6, 9]]


def test_insert_no_overlap():
    assert insert([[1, 2], [5, 6]], [3, 4]) == [[1, 2], [3, 4], [5, 6]]
