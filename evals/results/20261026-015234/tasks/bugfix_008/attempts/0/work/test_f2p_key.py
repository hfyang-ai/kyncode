from work import get_value


def test_get_by_param():
    assert get_value({'x': 1}, 'x') == 1
