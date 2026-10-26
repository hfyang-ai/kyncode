from semver import parse


def test_parse():
    assert parse('10.20.30') == (10, 20, 30)
