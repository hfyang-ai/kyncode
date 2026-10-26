from dedupe import first_occurrence


def test_first():
    assert first_occurrence([5, 6, 5], 5) == 0
