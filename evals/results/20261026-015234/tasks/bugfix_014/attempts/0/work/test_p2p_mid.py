from work import clamp


def test_clamp_mid():
    assert clamp(5, 0, 10) == 5
