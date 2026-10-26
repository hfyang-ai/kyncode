import yaml


def test_dev():
    d = yaml.safe_load(open('dev.yaml'))
    assert d['port'] == 8000
