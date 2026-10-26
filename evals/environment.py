"""Environment Manager：任务之间的环境隔离与快照。

长文方案「Environment Manager」：评测环境必须保证任务之间互不污染——
同一道题先后执行多次，第二次必须恢复到完全相同的初始状态。

这里采用「临时目录 + 初始快照」方案（Apple Silicon 本地的轻量选择）：
- 每次 attempt 一个独立目录 ``run/tasks/<id>/attempts/<n>/``；
- ``work/`` 是 agent 的工作区（setup 的副本，agent 在此修改）；
- ``snapshot/`` 是 setup 的不可变副本，用于生成 patch.diff 与复现。
"""

from __future__ import annotations

import difflib
import shutil
from dataclasses import dataclass
from pathlib import Path

from tasklib import TaskSpec


@dataclass
class Workspace:
    """一次 attempt 的隔离工作区。"""

    root: Path  # run/tasks/<id>/attempts/<n>
    work: Path  # agent 工作目录（setup 副本，agent 在此修改）
    initial_snapshot: Path  # setup 的不可变快照（用于 diff / 复现）


class EnvironmentManager:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def task_dir(self, task_id: str) -> Path:
        return self.run_dir / "tasks" / task_id

    def prepare(self, spec: TaskSpec, attempt_index: int) -> Workspace:
        """为一次 attempt 准备隔离工作区：work + 初始快照。"""
        attempt_dir = self.task_dir(spec.id) / "attempts" / str(attempt_index)
        work = attempt_dir / "work"
        snapshot = attempt_dir / "snapshot"

        for d in (work, snapshot):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

        if spec.setup_dir.exists():
            for item in spec.setup_dir.iterdir():
                dst_work = work / item.name
                dst_snap = snapshot / item.name
                if item.is_dir():
                    shutil.copytree(item, dst_work)
                    shutil.copytree(item, dst_snap)
                else:
                    shutil.copy2(item, dst_work)
                    shutil.copy2(item, dst_snap)

        return Workspace(root=attempt_dir, work=work, initial_snapshot=snapshot)

    def diff(self, ws: Workspace) -> str:
        """生成 work 相对初始快照的 unified diff（即 agent 的补丁）。

        应在 grader 注入隐藏测试之前调用，保证补丁只包含 agent 的真实改动。
        """
        snap, work = ws.initial_snapshot, ws.work
        snap_files = {p.relative_to(snap) for p in snap.rglob("*") if p.is_file()}
        work_files = {p.relative_to(work) for p in work.rglob("*") if p.is_file()}
        chunks: list[str] = []

        for rel in sorted(snap_files | work_files):
            if rel.name == "config.yaml":
                continue  # adapter 注入的 agent 配置，不算 agent 的代码改动
            if rel.parts and rel.parts[0] == ".kyncode":
                continue  # agent 运行时产物（日志 / session 状态），非任务代码改动
            sp = snap / rel
            wp = work / rel
            sp_text = _read(sp)
            wp_text = _read(wp)
            if sp_text == wp_text:
                continue
            chunks.append(
                "".join(
                    difflib.unified_diff(
                        sp_text.splitlines(keepends=True),
                        wp_text.splitlines(keepends=True),
                        fromfile=f"a/{rel}",
                        tofile=f"b/{rel}",
                    )
                )
            )
        return "".join(chunks)


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
