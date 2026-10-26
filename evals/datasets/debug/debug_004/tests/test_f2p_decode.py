from work import decode_utf8


def test_invalid_bytes():
    assert decode_utf8(b'hello\xffworld') == 'hello\ufffdworld'


def test_valid_utf8():
    assert decode_utf8('你好'.encode('utf-8')) == '你好'


def test_ascii():
    assert decode_utf8(b'abc') == 'abc'
