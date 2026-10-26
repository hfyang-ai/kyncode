from calc import add


def test_add_renamed():
    assert add(1, 2) == 3


def test_foo_gone():
    import calc
    assert not hasattr(calc, 'foo')
