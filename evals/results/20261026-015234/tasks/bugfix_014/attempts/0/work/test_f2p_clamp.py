from work import clamp


def test_clamp_upper():
    assert clamp(20, 0, 10) == 10


def test_clamp_lower():
    assert clamp(-5, 0, 10) == 0
