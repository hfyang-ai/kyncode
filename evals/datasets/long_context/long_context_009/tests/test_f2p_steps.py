from pipeline import step1, step2


def test_step1():
    assert step1([' a ', ' b ']) == ['a', 'b']


def test_step2():
    assert step2(['a', '', 'b']) == ['a', 'b']
