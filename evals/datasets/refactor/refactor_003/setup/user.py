from order import get_order_user


def get_username(uid):
    return f'u{uid}'


def orders_of(uid):
    return get_order_user(uid)
