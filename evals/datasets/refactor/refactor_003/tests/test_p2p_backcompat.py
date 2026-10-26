from app import user_label, order_label


def test_backcompat():
    assert user_label(3) == 'user:u3'
    assert order_label(4) == 'order:user-of-4'
