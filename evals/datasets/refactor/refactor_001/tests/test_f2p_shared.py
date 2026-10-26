from shared import process


def test_shared():
    assert process(' hi ') == 'HI'
