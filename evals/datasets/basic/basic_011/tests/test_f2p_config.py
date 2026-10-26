import server
import config


def test_port_from_config():
    assert server.get_port() == config.PORT


def test_port_value():
    assert server.get_port() == 8000
