from filesystem import count_files


def test_count():
    tree = [{'type': 'file', 'size': 1}, {'type': 'file', 'size': 2}]
    assert count_files(tree) == 2
