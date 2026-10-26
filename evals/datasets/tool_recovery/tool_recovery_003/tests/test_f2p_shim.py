from legacy_user import read_host


def test_read_host():
    assert read_host() == 'localhost'


def test_shim_direct():
    from legacy import get_value
    assert get_value('port') == 8080
