import constants


def test_token_exists():
    assert constants.TOKEN == 'sk-old'


def test_api_key_gone():
    assert not hasattr(constants, 'API_KEY')
