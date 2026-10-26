from pricing import format_currency
from inventory import get_name


def test_format():
    assert format_currency(20) == '$20.00'


def test_name():
    assert get_name('a') == 'Widget'
