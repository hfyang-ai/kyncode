from work import extract_digits


def test_no_digits():
    assert extract_digits('xyz') == []
