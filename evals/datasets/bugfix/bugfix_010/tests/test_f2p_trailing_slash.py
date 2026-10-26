from work import config_path


def test_trailing_slash():
    assert config_path('/etc/') == '/etc/config.yaml'
