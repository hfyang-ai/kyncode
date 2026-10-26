from config_store import get


def test_no_pollution():
    a = get('svc')
    a['tags'].append('polluted')
    b = get('svc')
    assert b['tags'] == ['a', 'b']


def test_top_level_independent():
    a = get('svc')
    a['name'] = 'changed'
    b = get('svc')
    assert b['name'] == 'svc'
