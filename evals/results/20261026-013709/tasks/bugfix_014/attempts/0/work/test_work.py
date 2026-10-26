from work import clamp


def test_clamp_upper():
    assert clamp(20, 0, 10) == 10
