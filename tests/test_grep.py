"""Grep 工具的单元测试：单文件检索（定向检索溢写文件）+ 目录递归检索。"""

from __future__ import annotations

from pathlib import Path

import pytest

from kyncode.tools.grep import Grep


async def _run(grep: Grep, **kwargs) -> str:
    result = await grep.execute(grep.params_model(**kwargs))
    assert not result.is_error
    return result.output


@pytest.mark.asyncio
async def test_grep_single_file_returns_line_content(tmp_path: Path):
    f = tmp_path / "spill.txt"
    f.write_text("alpha\nbeta\nalphabet\nomega\n", encoding="utf-8")

    out = await _run(Grep(), pattern="alpha", path=str(f))

    # 单文件返回 line:content，不带 file: 前缀
    assert out == "1:alpha\n3:alphabet"


@pytest.mark.asyncio
async def test_grep_single_file_no_match(tmp_path: Path):
    f = tmp_path / "spill.txt"
    f.write_text("alpha\nbeta\n", encoding="utf-8")

    out = await _run(Grep(), pattern="zzz", path=str(f))
    assert out == "No matches found."


@pytest.mark.asyncio
async def test_grep_directory_recursive(tmp_path: Path):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("foo bar\n", encoding="utf-8")

    out = await _run(Grep(), pattern="foo", path=str(tmp_path))

    assert "a.py:1:def foo():" in out
    assert "b.txt:1:foo bar" in out


@pytest.mark.asyncio
async def test_grep_single_file_not_found(tmp_path: Path):
    result = await Grep().execute(
        Grep.params_model(pattern="x", path=str(tmp_path / "nope.txt"))
    )
    assert result.is_error
    assert "not found" in result.output
