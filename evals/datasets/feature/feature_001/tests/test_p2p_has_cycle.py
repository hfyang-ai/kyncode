from scheduler import has_cycle


def test_has_cycle_false():
    assert has_cycle({}) is False
