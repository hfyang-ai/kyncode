from work import parse_and_add


def test_add_strings_as_numbers():
    assert parse_and_add('1', '2') == 3
