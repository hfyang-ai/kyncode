from config import fetch


def test_fetch():
    assert fetch('host') == 'localhost'
