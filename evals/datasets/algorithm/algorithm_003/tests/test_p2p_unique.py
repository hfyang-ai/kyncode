from substring import is_unique


def test_is_unique():
    assert is_unique('abc') is True
    assert is_unique('aab') is False
