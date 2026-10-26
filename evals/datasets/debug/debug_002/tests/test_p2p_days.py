from work import days_in_month, days_between


def test_thirty():
    assert days_in_month(2024, 4) == 30


def test_thirty_one():
    assert days_in_month(2024, 1) == 31


def test_days_between():
    assert days_between(2024, 1, 1, 2024, 1, 10) == 9
