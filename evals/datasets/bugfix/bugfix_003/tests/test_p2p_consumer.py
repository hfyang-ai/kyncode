from consumer import snapshot


def test_snapshot():
    s = snapshot('svc')
    assert s['name'] == 'svc'
    assert s['tags'] == ['a', 'b']
