import config


def test_config_unchanged():
    assert config.PORT == 8000
