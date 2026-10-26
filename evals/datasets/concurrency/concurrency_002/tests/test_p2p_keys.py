from kv import keys_of


def test_keys():
    assert keys_of({'a': 1, 'b': 2}) == ['a', 'b']
