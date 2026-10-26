from filesystem import total_size


def test_nested():
    tree = [
        {'type': 'dir', 'children': [
            {'type': 'file', 'size': 10},
            {'type': 'dir', 'children': [{'type': 'file', 'size': 5}]},
        ]},
        {'type': 'file', 'size': 7},
    ]
    assert total_size(tree) == 22


def test_empty_dir():
    assert total_size([{'type': 'dir', 'children': []}]) == 0
