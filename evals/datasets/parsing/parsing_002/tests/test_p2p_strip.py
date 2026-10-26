from iniparse import strip_comment


def test_strip():
    assert strip_comment('  key = value  # note') == 'key = value'
