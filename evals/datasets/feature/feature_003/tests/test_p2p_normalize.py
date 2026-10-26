from bus import normalize


def test_normalize():
    assert normalize('  Save ') == 'save'
