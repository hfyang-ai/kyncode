"""自动评分：对 agent 执行后的工作区判定是否解决。

分层判分（长文方案「Grader & Validator」），按顺序短路返回：

- Layer 1 执行有效性：代码是否可解析（语法错误 = 补丁不可应用）
- Layer 3 修改约束：是否篡改了禁止文件（grader.forbidden_files）
- Layer 2 功能正确性：隐藏测试 F2P / P2P（核心通过条件）

三种判分类型（由 tasks/<id>.yaml 的 grader.type 决定）：

- ``fail_to_pass``  —— 真实代码修改任务：运行隐藏 pytest，按文件名前缀分组
    - ``test_f2p_*.py`` → FAIL_TO_PASS（修复前失败、修复后应通过）
    - ``test_p2p_*.py`` → PASS_TO_PASS（修复前后都应通过的回归测试）
    resolved = F2P 全过 && P2P 全过
- ``answer_match`` —— 能力探测任务：用正则匹配 agent 最终输出
- ``file_assert``  —— 检查工作区某文件内容是否包含期望片段

注意：功能正确性才是核心通过条件；Layer 1/3 是前置硬约束（语法错、篡改
测试/禁止文件直接判失败）。代码风格、是否用特定工具等不设硬性门槛。
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from tasklib import GraderResult, TaskSpec

PYTEST_TIMEOUT = 120  # 单组 pytest 运行超时（秒）


def grade(spec: TaskSpec, workspace: Path, final_text: str) -> GraderResult:
    gtype = spec.grader.get("type", "fail_to_pass")
    if gtype == "answer_match":
        return _grade_answer_match(spec, final_text)
    if gtype == "file_assert":
        return _grade_file_assert(spec, workspace)
    return _grade_fail_to_pass(spec, workspace)


def _grade_answer_match(spec: TaskSpec, final_text: str) -> GraderResult:
    pattern = spec.grader.get("answer_pattern", "")
    if not pattern:
        return GraderResult(resolved=False, detail="grader 缺少 answer_pattern")
    try:
        matched = re.search(pattern, final_text or "", re.DOTALL) is not None
    except re.error as e:
        return GraderResult(resolved=False, detail=f"answer_pattern 非法: {e}")
    return GraderResult(
        resolved=matched,
        answer_matched=matched,
        detail=f"answer_pattern={pattern!r}",
    )


def _grade_file_assert(spec: TaskSpec, workspace: Path) -> GraderResult:
    fname = spec.grader.get("file", "")
    needle = spec.grader.get("contains", "")
    target = workspace / fname
    if not target.exists():
        return GraderResult(resolved=False, detail=f"文件不存在: {fname}")
    content = target.read_text(encoding="utf-8", errors="ignore")
    ok = needle in content
    return GraderResult(resolved=ok, detail=f"file={fname} contains={needle!r}")


def _grade_fail_to_pass(spec: TaskSpec, workspace: Path) -> GraderResult:
    # Layer 1：执行有效性 —— 代码语法错误视为补丁不可应用，直接失败
    syntax_errors = _check_syntax(workspace)
    if syntax_errors:
        return GraderResult(
            resolved=False,
            valid=False,
            detail=f"代码语法错误: {'; '.join(syntax_errors[:3])}",
        )

    # Layer 3：修改约束 —— 篡改禁止文件直接失败
    violations = _check_constraints(spec, workspace)
    if violations:
        return GraderResult(
            resolved=False,
            constraint_violations=violations,
            detail=f"违反修改约束: {'; '.join(violations[:3])}",
        )

    # Layer 2：功能正确性 —— 隐藏测试 F2P / P2P
    tests_dir = spec.tests_dir
    f2p_files = sorted(p.name for p in tests_dir.glob("test_f2p_*.py"))
    p2p_files = sorted(p.name for p in tests_dir.glob("test_p2p_*.py"))
    if not f2p_files and not p2p_files:
        return GraderResult(resolved=False, detail="没有隐藏测试（无 test_f2p_/test_p2p_ 文件）")

    # 把隐藏测试拷进工作区，让 pytest 能 import 到 setup 里的模块
    for p in tests_dir.glob("*.py"):
        dst = workspace / p.name
        dst.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")

    f2p_failed = _run_pytest(workspace, f2p_files) if f2p_files else []
    p2p_failed = _run_pytest(workspace, p2p_files) if p2p_files else []

    resolved = not f2p_failed and not p2p_failed
    return GraderResult(
        resolved=resolved,
        fail_to_pass_passed=["F2P"] if not f2p_failed else [],
        pass_to_pass_passed=["P2P"] if not p2p_failed else [],
        failed=f2p_failed + p2p_failed,
        p2p_failed=p2p_failed,
        detail=(
            f"F2P {'通过' if not f2p_failed else f'未通过({len(f2p_failed)})'}，"
            f"P2P {'通过' if not p2p_failed else f'未通过({len(p2p_failed)})'}"
        ),
    )


def _check_syntax(workspace: Path) -> list[str]:
    """Layer 1：检查工作区所有 .py 文件是否可解析。"""
    errors: list[str] = []
    for py in workspace.rglob("*.py"):
        try:
            ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError as e:
            errors.append(f"{py.name}:{e.lineno}")
    return errors


def _check_constraints(spec: TaskSpec, workspace: Path) -> list[str]:
    """Layer 3：检查 agent 是否篡改了禁止修改的文件。

    ``grader.forbidden_files`` 是相对 setup 根的文件路径列表。
    通过对比 setup 初始版本与工作区版本判断是否被改动。
    """
    forbidden = spec.grader.get("forbidden_files", []) or []
    violations: list[str] = []
    for rel in forbidden:
        setup_f = spec.setup_dir / rel
        work_f = workspace / rel
        if not setup_f.exists() and not work_f.exists():
            continue
        if not setup_f.exists() and work_f.exists():
            violations.append(f"新增禁止文件 {rel}")
        elif setup_f.exists() and work_f.exists():
            if setup_f.read_text(encoding="utf-8", errors="ignore") != work_f.read_text(
                encoding="utf-8", errors="ignore"
            ):
                violations.append(f"篡改禁止文件 {rel}")
    return violations


def _run_pytest(workspace: Path, test_files: list[str]) -> list[str]:
    """跑一组 pytest 文件，返回失败的测试节点名列表。"""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *test_files,
        "-q",
        "--tb=no",
        "--no-header",
        "-p",
        "no:cacheprovider",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=PYTEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return [f"<pytest 超时 {PYTEST_TIMEOUT}s>: {', '.join(test_files)}"]
    except OSError as e:
        return [f"<pytest 启动失败: {e}>"]

    failed: list[str] = []
    for line in (proc.stdout + "\n" + proc.stderr).splitlines():
        line = line.strip()
        if line.startswith("FAILED"):
            # 形如 "FAILED test_x.py::test_y" 或 "FAILED ../../test_x.py::test_y"
            failed.append(line.split("::")[-1] if "::" in line else line)
    return failed
