from csvparse import split_plain


def test_split():
    assert split_plain('x,y') == ['x', 'y']
