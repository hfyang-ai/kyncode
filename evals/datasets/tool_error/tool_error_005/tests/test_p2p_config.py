from config import DATA


def test_data():
    assert DATA['name'] == 'alice'
