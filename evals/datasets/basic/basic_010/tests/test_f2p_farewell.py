from text import farewell


def test_farewell():
    assert farewell('World') == 'Goodbye, World!'
