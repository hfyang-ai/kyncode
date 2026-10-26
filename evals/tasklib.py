"""evals 核心库：任务加载、沙箱准备、执行结果模型。

任务采用两段式结构：

- ``tasks/<task_id>.yaml`` —— 任务定义（prompt、判分配置、超时、重试）
- ``datasets/<category>/<task_id>/`` —— 题面数据（setup 初始代码 + tests 隐藏测试）

一个 task 的完整生命周期：

    discover -> prepare_workspace(复制 setup) -> run(agent 在 workspace 执行)
             -> grade(隐藏测试评分) -> metrics(汇总) -> report(报告)
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@dataclass
class EvalConfig:
    """evals/config.yaml 的加载结果。"""

    agent_bin: str = "kyncode"  # 驱动执行的 CLI（默认 kyncode，可换成 kai 等）
    agent_config: str = ""  # 待测 agent 的 config.yaml 路径（adapter 注入到 workspace）
    default_timeout_seconds: int = 600
    default_retries: int = 0
    work_root: str = "results"
    pricing: dict[str, float] | None = None  # {"input_per_1m": ..., "output_per_1m": ...}
    agent_version: str = ""  # 待测 Agent 版本标识，写入 manifest 便于版本对比
    model: str = ""  # 待测模型标识，写入 manifest 便于公平对比

    @classmethod
    def load(cls, path: Path | None = None) -> "EvalConfig":
        cfg_path = path or Path(__file__).parent / "config.yaml"
        raw: dict[str, Any] = {}
        if cfg_path.exists():
            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return cls(
            agent_bin=raw.get("agent_bin", "kyncode"),
            agent_config=str(raw.get("agent_config", "")),
            default_timeout_seconds=int(raw.get("default_timeout_seconds", 600)),
            default_retries=int(raw.get("default_retries", 0)),
            work_root=raw.get("work_root", "results"),
            pricing=raw.get("pricing"),
            agent_version=str(raw.get("agent_version", "")),
            model=str(raw.get("model", "")),
        )


# ---------------------------------------------------------------------------
# 任务模型
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """任务执行状态机。

    区分三种终止性质（长文方案「Evaluation Orchestrator」）：
    - COMPLETED：评分器已完成判分（结果可能通过，也可能失败）
    - AGENT_ERROR：Agent 本身运行失败（内部异常 / 无效工具调用导致终止）
    - INFRA_ERROR：环境 / 网络 / 镜像构建等基础设施出错，无法可靠判定 Agent 能力
    - TIMEOUT：超时（单独记录，不并入上面三类）
    """

    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    GRADING = "grading"
    COMPLETED = "completed"
    AGENT_ERROR = "agent_error"
    INFRA_ERROR = "infra_error"
    TIMEOUT = "timeout"


@dataclass
class TaskSpec:
    """从 tasks/*.yaml 加载的单道题定义。"""

    id: str
    category: str
    kind: str  # code_change | answer_match | file_assert
    title: str
    prompt: str
    dataset_dir: Path
    setup_dir: Path
    tests_dir: Path
    timeout_seconds: int
    max_retries: int
    grader: dict[str, Any] = field(default_factory=dict)
    dataset_version: str = ""  # 数据集版本锚点（为空 = 未版本化）
    difficulty: str = ""  # easy | medium | hard（为空 = 未标注）
    labels: list[str] = field(default_factory=list)  # 额外标签（如 smoke / regression）

    @property
    def tasks_file(self) -> Path:
        return self.dataset_dir.parent.parent.parent / "tasks" / f"{self.id}.yaml"

    @classmethod
    def load(cls, task_file: Path, datasets_root: Path) -> "TaskSpec":
        raw = yaml.safe_load(task_file.read_text(encoding="utf-8")) or {}
        task_id = raw.get("id") or task_file.stem
        category = raw.get("category", "basic")
        dataset_dir = datasets_root / category / task_id
        return cls(
            id=task_id,
            category=category,
            kind=raw.get("kind", "code_change"),
            title=raw.get("title", task_id),
            prompt=str(raw.get("prompt", "")).strip(),
            dataset_dir=dataset_dir,
            setup_dir=dataset_dir / "setup",
            tests_dir=dataset_dir / "tests",
            timeout_seconds=int(raw.get("timeout_seconds", 600)),
            max_retries=int(raw.get("max_retries", 0)),
            grader=raw.get("grader", {}) or {},
            dataset_version=str(raw.get("dataset_version", "")),
            difficulty=str(raw.get("difficulty", "")),
            labels=list(raw.get("labels", []) or []),
        )


@dataclass
class GraderResult:
    """单次尝试的评分结果（分层判分，见 grader.py）。"""

    resolved: bool = False
    fail_to_pass_passed: list[str] = field(default_factory=list)
    pass_to_pass_passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)  # F2P + P2P 失败测试名合并
    p2p_failed: list[str] = field(default_factory=list)  # 仅 P2P 失败（回归破坏）
    detail: str = ""
    infra_failure: bool = False  # Docker/API 等基础设施故障，非 Agent 能力问题
    answer_matched: bool = False
    valid: bool = True  # Layer 1 执行有效性（代码可解析 / 补丁可应用）
    constraint_violations: list[str] = field(default_factory=list)  # Layer 3 修改约束违规


@dataclass
class Attempt:
    """一次 agent 执行尝试。Pass@1 看 index == 0 的那次。"""

    index: int
    task_id: str
    success: bool
    infra_failure: bool
    timed_out: bool
    duration_seconds: float
    input_tokens: int
    output_tokens: int
    num_turns: int
    tool_calls: list[dict[str, Any]]
    errors: list[str]
    stop_reason: str
    final_text: str
    raw_ndjson: list[str] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)  # 精炼工具轨迹（LLM 失败分析用）
    grade: GraderResult = field(default_factory=GraderResult)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TaskOutcome:
    """一个任务的全部尝试 + 最终判定。"""

    spec: TaskSpec
    attempts: list[Attempt] = field(default_factory=list)

    @property
    def first_attempt(self) -> Attempt | None:
        return self.attempts[0] if self.attempts else None

    @property
    def best_attempt(self) -> Attempt | None:
        """按 success 优先、再按尝试顺序取最优。"""
        if not self.attempts:
            return None
        return max(self.attempts, key=lambda a: (a.success, -a.index))

    @property
    def resolved(self) -> bool:
        return self.best_attempt is not None and self.best_attempt.success

    @property
    def infra_failure(self) -> bool:
        """任一尝试因基础设施故障失败，则整个任务标记为基础设施故障。"""
        return any(a.infra_failure for a in self.attempts)

    @property
    def timed_out(self) -> bool:
        return any(a.timed_out for a in self.attempts)

    @property
    def status(self) -> TaskStatus:
        """按状态机推导任务的最终状态。

        判定顺序：completed(通过) → infra_error → timeout → agent_error → completed(未通过)。
        其中「Agent 异常终止」与「评分完成但未通过」的区分依据：
        Agent 有 error 且没有产生任何最终回答 → 视为 agent_error；
        否则评分器已完成判分 → completed（可能未通过）。
        """
        if not self.attempts:
            return TaskStatus.PENDING
        if self.resolved:
            return TaskStatus.COMPLETED
        if self.infra_failure:
            return TaskStatus.INFRA_ERROR
        if self.timed_out:
            return TaskStatus.TIMEOUT
        best = self.best_attempt
        if best is not None and best.errors and not best.final_text:
            return TaskStatus.AGENT_ERROR
        return TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# 任务发现
# ---------------------------------------------------------------------------


def discover_tasks(tasks_dir: Path, datasets_root: Path) -> list[TaskSpec]:
    """扫描 tasks/*.yaml，返回按 id 排序的任务列表。"""
    specs = [
        TaskSpec.load(f, datasets_root)
        for f in sorted(tasks_dir.glob("*.yaml"))
    ]
    return specs


# ---------------------------------------------------------------------------
# 沙箱
# ---------------------------------------------------------------------------


def prepare_workspace(spec: TaskSpec, run_dir: Path) -> Path:
    """把 setup/ 复制到独立工作区，agent 在这个目录里运行，避免污染题面。"""
    workspace = run_dir / "work" / spec.id
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    if spec.setup_dir.exists():
        for item in spec.setup_dir.iterdir():
            dst = workspace / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
    return workspace


# ---------------------------------------------------------------------------
# 结果读写
# ---------------------------------------------------------------------------


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
