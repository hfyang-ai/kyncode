from substring import longest_unique_substring


def test_basic():
    assert longest_unique_substring('abcabcbb') == 3


def test_all_same():
    assert longest_unique_substring('bbbbb') == 1


def test_end():
    assert longest_unique_substring('pwwkew') == 3


def test_empty():
    assert longest_unique_substring('') == 0


def test_all_unique():
    assert longest_unique_substring('abcdef') == 6
