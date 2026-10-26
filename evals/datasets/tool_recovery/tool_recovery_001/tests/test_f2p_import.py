import a


def test_get_a():
    assert a.get_a() == 'abc'


def test_modules_importable():
    import b, c
    assert b.get_b() == 'bc'
    assert c.get_c() == 'c'
