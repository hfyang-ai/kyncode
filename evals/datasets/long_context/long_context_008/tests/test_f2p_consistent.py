from config_a import VERSION as VA
from config_b import VERSION as VB


def test_consistent():
    assert VA == VB == '2.0'
