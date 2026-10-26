from api import get_user


def show():
    u = get_user(1)
    return u['name']
