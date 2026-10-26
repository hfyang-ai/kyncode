from evaluator import evaluate


def test_basic():
    assert evaluate('1+2') == 3


def test_precedence():
    assert evaluate('2+3*4') == 14


def test_parens():
    assert evaluate('(2+3)*4') == 20


def test_division():
    assert evaluate('7-3') == 4
    assert evaluate('8/2') == 4


def test_nested():
    assert evaluate('2*(3+(4-1))') == 12
