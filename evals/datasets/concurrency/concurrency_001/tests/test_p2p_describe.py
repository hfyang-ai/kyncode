from counter import describe


def test_describe():
    assert describe() == 'thread-safe counter'
