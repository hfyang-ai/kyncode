from pipeline import run


def test_full_pipeline():
    assert run(0) == 190


def test_other_input():
    assert run(5) == 195
