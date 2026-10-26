from order import advance, cancel


def test_legal_path():
    assert advance('pending', 'paid') == 'paid'
    assert advance('paid', 'shipped') == 'shipped'


def test_delivered_cannot_cancel():
    assert cancel('delivered') == 'delivered'


def test_shipped_cannot_cancel():
    assert cancel('shipped') == 'shipped'


def test_illegal_advance_rejected():
    assert advance('pending', 'shipped') == 'pending'
    assert advance('cancel', 'paid') == 'cancel'
