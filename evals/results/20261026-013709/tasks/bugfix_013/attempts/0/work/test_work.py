import pytest
from work import parse_int


def test_parse_raises():
    with pytest.raises(ValueError):
        parse_int('abc')
