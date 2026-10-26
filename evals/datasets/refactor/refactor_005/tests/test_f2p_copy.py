import settings


def test_returns_copy():
    c = settings.get_config()
    c['debug'] = True
    assert settings.get_config()['debug'] is False
