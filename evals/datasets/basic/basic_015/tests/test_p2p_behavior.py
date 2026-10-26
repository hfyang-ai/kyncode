import funcs


def test_concat():
    assert funcs.concat('a', 'b') == 'ab'


def test_is_positive():
    assert funcs.is_positive(5) is True
