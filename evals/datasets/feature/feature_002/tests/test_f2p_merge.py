from configmerge import merge_configs


def test_deep():
    base = {'db': {'host': 'localhost', 'port': 5432}, 'debug': False}
    prod = {'db': {'host': 'db.prod', 'pool': 10}, 'debug': True}
    merged = merge_configs(base, prod)
    assert merged == {'db': {'host': 'db.prod', 'port': 5432, 'pool': 10}, 'debug': True}


def test_no_mutate():
    base = {'db': {'host': 'a'}}
    override = {'db': {'port': 1}}
    merge_configs(base, override)
    assert base == {'db': {'host': 'a'}}


def test_three_layers():
    a = {'x': 1}
    b = {'x': 2, 'y': 3}
    c = {'z': 4}
    assert merge_configs(a, b, c) == {'x': 2, 'y': 3, 'z': 4}
