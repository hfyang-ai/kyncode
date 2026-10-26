from app import auth


def test_auth_returns_api_key():
    assert auth() == 'sk-secret-123'
