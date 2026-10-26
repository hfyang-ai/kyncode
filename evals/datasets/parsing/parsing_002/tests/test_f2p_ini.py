from iniparse import parse_ini


def test_sections():
    text = '[db]\nhost = localhost\nport = 5432\n\n[app]\ndebug = true\n'
    assert parse_ini(text) == {'db': {'host': 'localhost', 'port': '5432'}, 'app': {'debug': 'true'}}


def test_comments():
    text = '[s]\n# comment\n; another\nkey = value\n'
    assert parse_ini(text) == {'s': {'key': 'value'}}


def test_empty():
    assert parse_ini('') == {}
