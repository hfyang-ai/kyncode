from unionfind import UnionFind


def test_union_connected():
    uf = UnionFind(5)
    uf.union(0, 1)
    uf.union(1, 2)
    assert uf.connected(0, 2) is True
    assert uf.connected(0, 3) is False


def test_transitive():
    uf = UnionFind(6)
    uf.union(0, 1)
    uf.union(2, 3)
    uf.union(1, 2)
    assert uf.connected(0, 3) is True
    assert uf.connected(4, 5) is False


def test_self():
    uf = UnionFind(3)
    assert uf.connected(1, 1) is True
