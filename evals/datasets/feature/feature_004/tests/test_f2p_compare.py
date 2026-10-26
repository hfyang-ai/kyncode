from semver import compare


def test_equal():
    assert compare('1.2.3', '1.2.3') == 0


def test_less():
    assert compare('1.2.3', '1.2.4') == -1
    assert compare('1.9.0', '2.0.0') == -1


def test_greater():
    assert compare('2.0.0', '1.9.9') == 1
    assert compare('1.2.10', '1.2.9') == 1
