from work import config_path


def test_plain():
    assert config_path('/etc') == '/etc/config.yaml'
