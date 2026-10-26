from text import greet


def test_greet_unchanged():
    assert greet('World') == 'Hello, World!'
