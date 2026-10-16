from __future__ import annotations

from pathlib import Path

from kyncode.tools.file_state_cache import FileStateCache


def test_record_and_check(tmp_path: Path) -> None:
    cache = FileStateCache()
    f = tmp_path / "a.txt"
    f.write_text("hello")
    cache.record(str(f), f.stat().st_mtime_ns)

    ok, err = cache.check(str(f))
    assert ok
    assert err == ""


def test_check_requires_read(tmp_path: Path) -> None:
    cache = FileStateCache()
    ok, err = cache.check(str(tmp_path / "nope.txt"))
    assert not ok
    assert "not been read" in err


def test_freshness_unchanged(tmp_path: Path) -> None:
    cache = FileStateCache()
    a = tmp_path / "a.txt"
    a.write_text("one")
    cache.record(str(a), a.stat().st_mtime_ns)

    unchanged, changed = cache.freshness()
    assert str(a) in unchanged
    assert changed == []


def test_freshness_changed(tmp_path: Path) -> None:
    cache = FileStateCache()
    b = tmp_path / "b.txt"
    b.write_text("two")
    # 记录一个与实际不符的 mtime，模拟「读取后文件又被修改」。
    cache.record(str(b), 1)

    unchanged, changed = cache.freshness()
    assert unchanged == []
    assert str(b) in changed


def test_freshness_missing_file_is_changed(tmp_path: Path) -> None:
    cache = FileStateCache()
    p = tmp_path / "gone.txt"
    p.write_text("x")
    cache.record(str(p), p.stat().st_mtime_ns)
    p.unlink()

    _, changed = cache.freshness()
    assert changed == [str(p)]


def test_roundtrip(tmp_path: Path) -> None:
    cache = FileStateCache()
    f = tmp_path / "a.txt"
    f.write_text("hello")
    cache.record(str(f), f.stat().st_mtime_ns)

    restored = FileStateCache.from_dict(cache.to_dict())
    assert restored._cache == cache._cache
    assert restored._read_at == cache._read_at


def test_load_dict_mutates_in_place(tmp_path: Path) -> None:
    cache = FileStateCache()
    f = tmp_path / "a.txt"
    f.write_text("hello")
    cache.record(str(f), f.stat().st_mtime_ns)

    target = FileStateCache()
    target.load_dict(cache.to_dict())
    assert target._cache == cache._cache
    assert target._read_at == cache._read_at


def test_from_dict_legacy_plain_values() -> None:
    cache = FileStateCache.from_dict({"/a": 123})
    assert cache._cache["/a"] == 123
    assert cache._read_at == {}
