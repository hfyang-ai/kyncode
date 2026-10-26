import pytest
from service import UserService


def test_register_and_find():
    s = UserService()
    u = s.register('alice', 'alice@x.com')
    assert s.find_by_email('alice@x.com') == u
    assert s.count() == 1


def test_duplicate_email():
    s = UserService()
    s.register('alice', 'a@x.com')
    with pytest.raises(ValueError):
        s.register('bob', 'a@x.com')


def test_invalid_name():
    s = UserService()
    with pytest.raises(ValueError):
        s.register('   ', 'a@x.com')


def test_invalid_email():
    s = UserService()
    with pytest.raises(ValueError):
        s.register('alice', 'nope')


def test_all_names_order():
    s = UserService()
    s.register('alice', 'a@x.com')
    s.register('bob', 'b@x.com')
    assert s.all_names() == ['alice', 'bob']
