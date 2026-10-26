import constants
import logic


def test_constant_moved():
    assert constants.MAX_RETRY == 3


def test_logic_uses_constant():
    assert logic.attempts() == constants.MAX_RETRY
