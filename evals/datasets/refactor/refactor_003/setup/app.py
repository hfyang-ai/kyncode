from user import get_username
from order import get_order_user


def user_label(uid):
    return f'user:{get_username(uid)}'


def order_label(oid):
    return f'order:{get_order_user(oid)}'
