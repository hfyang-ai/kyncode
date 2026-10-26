from evaluator import tokenize


def test_tokenize():
    assert tokenize('1 + 2') == ['1', '+', '2']
