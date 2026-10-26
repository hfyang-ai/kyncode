from stage_00 import transform as t0
from stage_19 import transform as tlast


def aggregate(x):
    return t0(x) + tlast(x)
