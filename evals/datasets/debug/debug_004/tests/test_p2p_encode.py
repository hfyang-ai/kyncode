from work import encode_utf8


def test_encode():
    assert encode_utf8('ok') == b'ok'
