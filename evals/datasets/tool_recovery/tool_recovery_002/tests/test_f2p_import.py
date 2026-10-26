import main


def test_main():
    assert main.main() == 6


def test_core():
    from mypkg.core import compute
    assert compute(5) == 10
