import yaml


def test_staging():
    d = yaml.safe_load(open('staging.yaml'))
    assert d['port'] == 8000


def test_prod():
    d = yaml.safe_load(open('prod.yaml'))
    assert d['port'] == 8000
