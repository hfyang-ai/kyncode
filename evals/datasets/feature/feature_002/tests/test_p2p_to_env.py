from configmerge import to_env


def test_to_env():
    assert to_env('APP', {'a': 1}) == {'APP_a': 1}
