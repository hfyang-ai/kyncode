from calc import total


def test_total_uses_tax_rate():
    assert abs(total(100) - 110) < 1e-9


def test_total_zero():
    assert total(0) == 0
