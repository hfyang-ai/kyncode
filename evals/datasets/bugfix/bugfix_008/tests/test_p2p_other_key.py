from work import get_value


def test_get_other_key():
    assert get_value({'a': 2}, 'a') == 2
