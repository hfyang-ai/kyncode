import inspect
from api import get_user


def test_has_include_avatar():
    sig = inspect.signature(get_user)
    assert 'include_avatar' in sig.parameters


def test_no_avatar():
    u = get_user(1, include_avatar=False)
    assert u['name'] == 'Alice'
