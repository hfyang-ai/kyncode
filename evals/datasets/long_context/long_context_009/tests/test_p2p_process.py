from pipeline import process


def test_process():
    assert process([' a ', '', ' b ']) == ['a', 'b']
