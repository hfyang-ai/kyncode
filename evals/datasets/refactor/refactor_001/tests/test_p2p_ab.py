from a import process as pa
from b import process as pb


def test_a():
    assert pa(' x ') == 'X'


def test_b():
    assert pb(' y ') == 'Y'
