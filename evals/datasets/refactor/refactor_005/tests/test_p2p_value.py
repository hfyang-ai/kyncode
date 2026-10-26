import settings


def test_default():
    assert settings.get_config()['debug'] is False
