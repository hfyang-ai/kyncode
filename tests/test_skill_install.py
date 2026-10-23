from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from kyncode.skills.install import (
    SkillSource,
    _resolve_source,
    install_skill,
    parse_skill_url,
)

# ── parse_skill_url：github.com 根路径 / tree-root 新形态 ──


def test_repo_root_url() -> None:
    src = parse_skill_url("https://github.com/ayghri/i-have-adhd")
    assert src.owner == "ayghri"
    assert src.repo == "i-have-adhd"
    assert src.ref == ""
    assert src.subpath is None
    assert src.name == "i-have-adhd"


def test_tree_root_url() -> None:
    src = parse_skill_url("https://github.com/ayghri/i-have-adhd/tree/main")
    assert src.ref == "main"
    assert src.subpath is None
    assert src.name == "i-have-adhd"


def test_tree_with_subpath_url() -> None:
    src = parse_skill_url(
        "https://github.com/ayghri/i-have-adhd/tree/main/skills/i-have-adhd"
    )
    assert src.ref == "main"
    assert src.subpath == "skills/i-have-adhd"
    assert src.name == "i-have-adhd"


def test_unsupported_github_path_raises() -> None:
    with pytest.raises(ValueError):
        parse_skill_url(
            "https://github.com/ayghri/i-have-adhd/blob/main/README.md"
        )


# ── mock GitHub API ──


def _entry(name: str, path: str, content: bytes) -> dict:
    return {
        "name": name,
        "path": path,
        "type": "file",
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content).decode(),
    }


_SKILL_BODY = b"---\nname: i-have-adhd\ndescription: test\n---\nbody\n"


def _repo_handler(
    root_has_skill: bool = False,
    skill_subdir: str | None = "skills/i-have-adhd",
    default_branch: str = "main",
):
    """模拟 ayghri/i-have-adhd 仓库的 Contents / repo 接口。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/ayghri/i-have-adhd":
            return httpx.Response(200, json={"default_branch": default_branch})

        prefix = "/repos/ayghri/i-have-adhd/contents/"
        if path.startswith(prefix):
            sub = path[len(prefix):].rstrip("/")
            if sub == "":
                entries = [_entry("README.md", "README.md", b"readme")]
                if root_has_skill:
                    entries.append(_entry("SKILL.md", "SKILL.md", _SKILL_BODY))
                return httpx.Response(200, json=entries)
            if sub == skill_subdir:
                return httpx.Response(
                    200, json=[_entry("SKILL.md", f"{sub}/SKILL.md", _SKILL_BODY)]
                )
            return httpx.Response(404, json={})

        return httpx.Response(404, json={})

    return handler


def _patch_client(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real(transport=transport, **kwargs)

    monkeypatch.setattr("kyncode.skills.install.httpx.AsyncClient", factory)


# ── _resolve_source：子目录探测 ──


async def test_resolve_detects_skills_subdir() -> None:
    transport = httpx.MockTransport(_repo_handler())
    async with httpx.AsyncClient(transport=transport) as client:
        src = SkillSource(
            owner="ayghri",
            repo="i-have-adhd",
            ref="",
            subpath=None,
            name="i-have-adhd",
            original="https://github.com/ayghri/i-have-adhd",
        )
        resolved = await _resolve_source(client, src)

    assert resolved.ref == "main"
    assert resolved.subpath == "skills/i-have-adhd"
    assert resolved.name == "i-have-adhd"


async def test_resolve_uses_root_when_skill_at_root() -> None:
    transport = httpx.MockTransport(_repo_handler(root_has_skill=True, skill_subdir=None))
    async with httpx.AsyncClient(transport=transport) as client:
        src = SkillSource(
            owner="ayghri",
            repo="i-have-adhd",
            ref="",
            subpath=None,
            name="i-have-adhd",
            original="https://github.com/ayghri/i-have-adhd",
        )
        resolved = await _resolve_source(client, src)

    assert resolved.subpath == ""
    assert resolved.name == "i-have-adhd"


async def test_resolve_raises_when_no_manifest_anywhere() -> None:
    transport = httpx.MockTransport(_repo_handler(skill_subdir=None))
    async with httpx.AsyncClient(transport=transport) as client:
        src = SkillSource(
            owner="ayghri",
            repo="i-have-adhd",
            ref="",
            subpath=None,
            name="i-have-adhd",
            original="https://github.com/ayghri/i-have-adhd",
        )
        with pytest.raises(RuntimeError, match="could not locate SKILL.md"):
            await _resolve_source(client, src)


# ── install_skill：根路径 URL 端到端 ──


async def test_install_from_repo_root_url(monkeypatch, tmp_path: Path) -> None:
    _patch_client(monkeypatch, _repo_handler())

    src = parse_skill_url("https://github.com/ayghri/i-have-adhd")
    report = await install_skill(src, install_root=tmp_path)

    assert report.skill_name == "i-have-adhd"
    assert (tmp_path / "i-have-adhd" / "SKILL.md").is_file()


async def test_install_resolves_default_branch(monkeypatch, tmp_path: Path) -> None:
    _patch_client(monkeypatch, _repo_handler(default_branch="master"))

    src = parse_skill_url("https://github.com/ayghri/i-have-adhd")
    report = await install_skill(src, install_root=tmp_path)

    assert report.skill_name == "i-have-adhd"
    assert (tmp_path / "i-have-adhd" / "SKILL.md").is_file()
