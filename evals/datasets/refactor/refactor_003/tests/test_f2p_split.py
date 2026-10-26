from users import user_label
from orders import order_label


def test_users():
    assert user_label(1) == 'user:u1'


def test_orders():
    assert order_label(2) == 'order:user-of-2'


def test_no_cycle_import():
    import user
    import order
    assert user.get_username(1) == 'u1'
    assert order.get_order_user(2) == 'user-of-2'
