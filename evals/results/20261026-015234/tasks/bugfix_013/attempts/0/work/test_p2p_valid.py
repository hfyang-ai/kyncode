from work import parse_int


def test_parse_valid():
    assert parse_int('42') == 42
