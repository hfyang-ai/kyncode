from utils import helper_a, helper_b


def test_helper_a():
    assert helper_a() == 'a'


def test_helper_b_reachable():
    assert helper_b() == 'b'
