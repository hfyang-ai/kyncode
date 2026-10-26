from toposort import build_graph


def test_build():
    assert build_graph([1], []) == {1: []}
