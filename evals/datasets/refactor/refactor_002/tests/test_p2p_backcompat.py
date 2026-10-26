from stats import add, mean, count


def test_module_funcs():
    s = count()
    add(4)
    assert count() == s + 1
    assert mean() == 4
