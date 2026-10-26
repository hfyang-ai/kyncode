from work import decode_utf8


def test_invalid_bytes():
    assert decode_utf8(b'hello\xffworld') == 'hello\ufffdworld'
