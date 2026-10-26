from work import extract_digits


def test_extract_digits():
    assert extract_digits('abc123') == ['123']
