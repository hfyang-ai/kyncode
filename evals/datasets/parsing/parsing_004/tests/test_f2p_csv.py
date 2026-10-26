from csvparse import parse_csv


def test_plain():
    assert parse_csv('a,b,c') == ['a', 'b', 'c']


def test_quoted_comma():
    assert parse_csv('a,"b,c",d') == ['a', 'b,c', 'd']


def test_escaped_quote():
    assert parse_csv('"say ""hi""",x') == ['say "hi"', 'x']


def test_empty_fields():
    assert parse_csv('a,,c') == ['a', '', 'c']
