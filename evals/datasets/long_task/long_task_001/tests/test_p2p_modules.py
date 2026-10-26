from repo import Repo
from validators import is_valid_email, is_valid_name


def test_repo():
    r = Repo()
    r.add({'id': 1, 'email': 'a@x.com'})
    assert r.find('a@x.com')['id'] == 1
    assert r.find('none') is None


def test_validators():
    assert is_valid_name('x') is True
    assert is_valid_name('  ') is False
    assert is_valid_email('a@b') is True
    assert is_valid_email('ab') is False
