"""Run Store：记录每次测评的完整实验配置、执行轨迹与评分产物。

长文方案「Run Store & Trace Collector」：
- ``manifest.json`` —— 完整实验配置（否则几个月后无法解释分数是怎么产生的）
- ``summary.json`` —— 运行汇总
- ``trace.jsonl`` —— 结构化事件流（llm_request / tool_call / tool_result /
  retry / agent_finished / ...），用于失败归因与 Agent 内部工程指标
- 每道题目录下保存 agent 原始 stdout/stderr、patch.diff、grader.json

attempt（执行事实）与 result（评分判定）分开落盘：agent 的原始日志与补丁
是事实，grader.json 是基于产物的判定，两者分离便于排查重试、复评与历史变化。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tasklib import save_json


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    """构造一条带时间戳的 trace 事件。"""
    return {"ts": now_iso(), "type": event_type, **fields}


class RunStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    # -- 目录 ----------------------------------------------------------------

    def task_dir(self, task_id: str) -> Path:
        return self.run_dir / "tasks" / task_id

    # -- 顶层文件 ------------------------------------------------------------

    def write_manifest(self, data: dict[str, Any]) -> None:
        save_json(self.run_dir / "manifest.json", data)

    def write_summary(self, data: dict[str, Any]) -> None:
        save_json(self.run_dir / "summary.json", data)

    # -- trace 事件流 --------------------------------------------------------

    def append_trace(self, task_id: str, event: dict[str, Any]) -> None:
        """向某道题的 trace.jsonl 追加一条结构化事件。"""
        p = self.task_dir(task_id) / "trace.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    # -- 每道题产物 ----------------------------------------------------------

    def write_agent_io(
        self, task_id: str, attempt_index: int, stdout: str, stderr: str
    ) -> None:
        d = self.task_dir(task_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"agent_stdout.attempt{attempt_index}.log").write_text(
            stdout, encoding="utf-8", errors="ignore"
        )
        (d / f"agent_stderr.attempt{attempt_index}.log").write_text(
            stderr, encoding="utf-8", errors="ignore"
        )

    def write_patch(self, task_id: str, attempt_index: int, patch: str) -> None:
        d = self.task_dir(task_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"patch.attempt{attempt_index}.diff").write_text(
            patch, encoding="utf-8", errors="ignore"
        )

    def write_grader(self, task_id: str, attempt_index: int, grader: dict[str, Any]) -> None:
        d = self.task_dir(task_id)
        d.mkdir(parents=True, exist_ok=True)
        save_json(d / f"grader.attempt{attempt_index}.json", grader)
