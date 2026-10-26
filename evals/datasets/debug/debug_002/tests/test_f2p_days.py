from work import days_in_month


def test_feb_common():
    assert days_in_month(2023, 2) == 28


def test_feb_leap():
    assert days_in_month(2024, 2) == 29


def test_century():
    assert days_in_month(2000, 2) == 29
    assert days_in_month(1900, 2) == 28
