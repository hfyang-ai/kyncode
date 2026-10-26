from order_state import display


def test_display():
    assert display('pending') == 'PENDING'
