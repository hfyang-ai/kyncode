import utils


def test_utils_has_normalize():
    assert utils.normalize('  Hi ') == 'hi'
