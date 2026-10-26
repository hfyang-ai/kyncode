"""Evaluation Orchestrator：测评的控制中心，状态机编排。

不负责「理解代码」，只负责可靠地执行测评生命周期（长文方案）：
加载任务 → 创建 run/attempt 标识 → 准备隔离环境 → 调用 Agent →
监控超时 → 运行独立评分器 → 持久化结果与轨迹 → 汇总。

关键职责：
- 显式区分 AgentError / InfraError / Timeout / Completed 四种终止性质。
- attempt（执行事实）与 result（评分判定）分离：agent 日志与补丁先落盘，
  再由独立 Grader 判定，二者互不依赖。
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapter import AgentAdapter, AgentRequest, CLIAgentAdapter
from environment import EnvironmentManager
from store import RunStore, make_event, now_iso
from tasklib import (
    Attempt,
    EvalConfig,
    GraderResult,
    TaskOutcome,
    TaskSpec,
)


@dataclass
class RunContext:
    """一次测评实验的上下文（对应 manifest 的核心字段）。"""

    run_id: str
    mode: str  # live | mock
    cfg: EvalConfig
    specs: list[TaskSpec]


class Orchestrator:
    def __init__(self, run_dir: Path, ctx: RunContext) -> None:
        self.run_dir = run_dir
        self.ctx = ctx
        self.store = RunStore(run_dir)
        self.env = EnvironmentManager(run_dir)
        self.adapter: AgentAdapter = CLIAgentAdapter(
            ctx.cfg.agent_bin, ctx.cfg.agent_config
        )

    # -- 顶层 ----------------------------------------------------------------

    def run_all(self) -> list[TaskOutcome]:
        self.write_manifest()
        outcomes: list[TaskOutcome] = []
        for spec in self.ctx.specs:
            outcomes.append(self.run_task(spec))
        self.write_summary(outcomes)
        return outcomes

    def run_task(self, spec: TaskSpec) -> TaskOutcome:
        outcome = TaskOutcome(spec=spec)
        total = spec.max_retries + 1
        for idx in range(total):
            self.store.append_trace(
                spec.id, make_event("attempt_started", attempt=idx, max_attempts=total)
            )
            attempt = self._run_attempt(spec, idx)
            outcome.attempts.append(attempt)
            if attempt.success:
                self.store.append_trace(
                    spec.id, make_event("agent_finished", attempt=idx, status="success")
                )
                break
            next_idx = idx + 1 if idx + 1 < total else None
            self.store.append_trace(
                spec.id, make_event("retry", attempt=idx, next=next_idx)
            )
        self.store.append_trace(
            spec.id,
            make_event(
                "task_finished",
                status=outcome.status.value,
                resolved=outcome.resolved,
            ),
        )
        return outcome

    # -- 单次 attempt --------------------------------------------------------

    def _run_attempt(self, spec: TaskSpec, idx: int) -> Attempt:
        ws = self.env.prepare(spec, idx)

        if self.ctx.mode == "mock":
            attempt = _mock_attempt(spec, idx)
            self.store.write_agent_io(spec.id, idx, "(mock) 合成执行\n", "")
            self.store.write_patch(spec.id, idx, "")
            self.store.write_grader(spec.id, idx, _grader_to_dict(attempt.grade))
            return attempt

        # live：调用 Agent，然后独立判分
        request = AgentRequest(
            task_id=spec.id,
            prompt=spec.prompt,
            workspace=ws.work,
            timeout_seconds=spec.timeout_seconds,
        )
        result = asyncio.run(self.adapter.run(request))

        self.store.write_agent_io(spec.id, idx, result.stdout, result.stderr)

        # 补丁须在 grader 注入隐藏测试之前生成，保证只含 agent 的真实改动
        patch = self.env.diff(ws) if spec.kind == "code_change" else ""
        self.store.write_patch(spec.id, idx, patch)

        infra = _detect_infra(
            result.errors, result.stderr, result.exit_code, result.timed_out
        )
        from grader import grade

        grader_result = grade(spec, ws.work, result.final_text)
        self.store.write_grader(spec.id, idx, _grader_to_dict(grader_result))

        return Attempt(
            index=idx,
            task_id=spec.id,
            success=grader_result.resolved and not infra and not result.timed_out,
            infra_failure=infra,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            num_turns=result.num_turns,
            tool_calls=result.tool_calls,
            errors=result.errors,
            stop_reason=result.stop_reason,
            final_text=result.final_text,
            raw_ndjson=result.raw_ndjson,
            grade=grader_result,
        )

    # -- manifest / summary --------------------------------------------------

    def write_manifest(self) -> None:
        cfg = self.ctx.cfg
        manifest: dict[str, Any] = {
            "run_id": self.ctx.run_id,
            "agent_bin": cfg.agent_bin,
            "agent_version": getattr(cfg, "agent_version", ""),
            "model": getattr(cfg, "model", ""),
            "mode": self.ctx.mode,
            "started_at": now_iso(),
            "config": {
                "default_timeout_seconds": cfg.default_timeout_seconds,
                "default_retries": cfg.default_retries,
                "work_root": cfg.work_root,
            },
            "tasks": [
                {
                    "id": s.id,
                    "category": s.category,
                    "kind": s.kind,
                    "difficulty": s.difficulty,
                    "dataset_version": s.dataset_version,
                }
                for s in self.ctx.specs
            ],
        }
        self.store.write_manifest(manifest)

    def write_summary(self, outcomes: list[TaskOutcome]) -> None:
        status_counts = Counter(o.status.value for o in outcomes)
        self.store.write_summary(
            {
                "run_id": self.ctx.run_id,
                "finished_at": now_iso(),
                "total_tasks": len(outcomes),
                "resolved": sum(1 for o in outcomes if o.resolved),
                "status_counts": dict(status_counts),
            }
        )


# ---------------------------------------------------------------------------
# 基础设施故障归因
# ---------------------------------------------------------------------------

_INFRA_KEYWORDS = (
    "api key", "authentication", "unauthorized", "401", "403",
    "rate limit", "rate_limit", "too many requests", "429",
    "connection", "timed out", "service unavailable", "503", "502",
    "docker", "context deadline", "internal server error",
)


def _detect_infra(
    errors: list[str], stderr: str, exit_code: int | None, timed_out: bool
) -> bool:
    """区分基础设施故障与 Agent 能力问题。

    超时单独统计（长文方案明确「超时应当单独记录」），不算基础设施故障。
    """
    if timed_out:
        return False
    blob = (" ".join(errors) + " " + stderr).lower()
    if any(k in blob for k in _INFRA_KEYWORDS):
        return True
    # 进程非 0 退出且没有任何错误信息，倾向判定为运行环境故障
    if exit_code not in (0, None) and not errors:
        return True
    return False


# ---------------------------------------------------------------------------
# mock 执行（离线合成，用于跑通全流程 / 演示报告）
# ---------------------------------------------------------------------------

_MOCK_FAIL_RATE = {
    "basic": 0.10,
    "bugfix": 0.20,
    "tool_error": 0.40,
    "long_context": 0.25,
    "refactor": 0.20,
}


def _mock_attempt(spec: TaskSpec, attempt_index: int) -> Attempt:
    # 稳定哈希：同一任务每次运行结果可复现
    h = int(hashlib.md5(f"{spec.id}#{attempt_index}".encode()).hexdigest()[:8], 16)
    bucket = (h % 100) / 100.0  # [0, 1)
    rng = random.Random(f"{spec.id}#{attempt_index}")
    fail_rate = _MOCK_FAIL_RATE[spec.category]

    timed_out = False
    infra = False
    success = True
    if bucket < 0.03:
        infra = True
        success = False
    elif bucket < 0.09:
        timed_out = True
        success = False
    elif bucket < 0.09 + fail_rate:
        success = False

    turns = rng.randint(2, 12)
    input_tokens = rng.randint(1500, 30000)
    output_tokens = rng.randint(300, 8000)
    duration = round(rng.uniform(8.0, 180.0), 2)
    if timed_out:
        duration = float(spec.timeout_seconds)

    errors: list[str] = []
    if infra:
        errors.append("API service unavailable (503)")

    grade = GraderResult(resolved=success, infra_failure=infra)
    if infra:
        grade.detail = "infra failure: API/Docker 服务不可用"
    elif timed_out:
        grade.detail = f"timed out (> {spec.timeout_seconds}s)"
    elif spec.kind == "answer_match":
        grade.answer_matched = success
        grade.detail = "mock: answer matched" if success else "mock: answer mismatch"
    elif spec.kind == "file_assert":
        grade.detail = "mock: file assert"
    else:
        if success:
            grade.fail_to_pass_passed = ["F2P"]
            grade.pass_to_pass_passed = ["P2P"]
            grade.detail = "mock: F2P 全过 + P2P 全过"
        elif rng.random() < 0.3:
            grade.failed = ["test_p2p_regression"]
            grade.p2p_failed = ["test_p2p_regression"]
            grade.detail = "mock: 修复过程中破坏了原有功能（P2P 失败）"
        else:
            grade.failed = ["test_f2p_target"]
            grade.detail = "mock: 未能通过 FAIL_TO_PASS 测试"

    return Attempt(
        index=attempt_index,
        task_id=spec.id,
        success=success,
        infra_failure=infra,
        timed_out=timed_out,
        duration_seconds=duration,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        num_turns=turns,
        tool_calls=[{"name": "ReadFile", "is_error": False} for _ in range(turns)],
        errors=errors,
        stop_reason="end_turn" if not timed_out else "timeout",
        final_text="mock result",
        grade=grade,
    )


def _grader_to_dict(gr: GraderResult) -> dict[str, Any]:
    return {
        "resolved": gr.resolved,
        "fail_to_pass_passed": gr.fail_to_pass_passed,
        "pass_to_pass_passed": gr.pass_to_pass_passed,
        "failed": gr.failed,
        "p2p_failed": gr.p2p_failed,
        "detail": gr.detail,
        "infra_failure": gr.infra_failure,
        "answer_matched": gr.answer_matched,
    }
