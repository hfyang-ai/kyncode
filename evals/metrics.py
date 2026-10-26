"""指标统计：从一批 TaskOutcome 计算追踪指标。

口径（与 README 一致）：

- 任务解决率   = resolved 任务数 ÷ 有效提交任务数（排除基础设施故障）
- Pass@1      = 第 1 次尝试成功任务数 ÷ 任务总数
- 多次尝试成功率 = 最终（含重试）resolved ÷ 任务总数（与 Pass@1 分开报告）
- 回归通过率   = PASS_TO_PASS 全过的 fail_to_pass 任务数 ÷ 有效 fail_to_pass 任务数
- 超时率      = 超时任务数 ÷ 任务总数
- 基础设施错误率 = 基础设施故障任务数 ÷ 任务总数
- 平均耗时    = 所有任务总耗时 ÷ 任务数
- P50/P95 耗时 = 所有 attempt 耗时的中位数 / 95 分位（揭示长尾）
- 平均 Token  = 总 Token ÷ 任务数
- LLM 请求数  = sum(num_turns)（每轮 agent 循环 = 一次 LLM 请求，近似）
- 工具调用数  = sum(len(tool_calls))
- 每次尝试成本 = 总成本 ÷ 总尝试数
- 每个成功任务成本 = 总成本 ÷ resolved 任务数（避免失败任务多时误导）

失败分类（长文方案「失败分析」统一分类体系）：8 类，启发式推断。
"""

from __future__ import annotations

from typing import Any

from tasklib import TaskOutcome

# 失败分类体系（长文方案第五节）
FAILURE_CATEGORIES = (
    "Planning Failure",
    "Context Failure",
    "Tool Failure",
    "Recovery Failure",
    "Coding Failure",
    "Verification Failure",
    "Budget / Timeout",
    "Infrastructure Failure",
)


def compute_metrics(
    outcomes: list[TaskOutcome], pricing: dict[str, float] | None = None
) -> dict[str, Any]:
    total = len(outcomes)

    infra = [o for o in outcomes if o.infra_failure]
    valid = [o for o in outcomes if not o.infra_failure]
    resolved = [o for o in outcomes if o.resolved]
    pass1 = [o for o in outcomes if o.first_attempt and o.first_attempt.success]
    timed_out = [o for o in outcomes if any(a.timed_out for a in o.attempts)]

    # 回归通过率：只统计有隐藏 P2P 测试的 fail_to_pass 任务
    code_change = [o for o in valid if o.spec.grader.get("type") == "fail_to_pass"]
    p2p_ok = [
        o
        for o in code_change
        if o.best_attempt is not None and not _p2p_failed(o.best_attempt)
    ]

    # 耗时 / Token / 成本 / 请求 / 工具调用：累加所有尝试
    total_attempts = sum(len(o.attempts) for o in outcomes)
    durations = sorted(a.duration_seconds for o in outcomes for a in o.attempts)
    total_duration = sum(durations)
    total_input = sum(a.input_tokens for o in outcomes for a in o.attempts)
    total_output = sum(a.output_tokens for o in outcomes for a in o.attempts)
    total_tokens = total_input + total_output
    total_llm_requests = sum(a.num_turns for o in outcomes for a in o.attempts)
    total_tool_calls = sum(len(a.tool_calls) for o in outcomes for a in o.attempts)

    cost_per_task = None
    cost_per_attempt = None
    cost_per_success = None
    total_cost = None
    if pricing:
        in_p = pricing.get("input_per_1m", 0.0)
        out_p = pricing.get("output_per_1m", 0.0)
        total_cost = (total_input * in_p + total_output * out_p) / 1e6
        cost_per_task = total_cost / total if total else 0.0
        cost_per_attempt = total_cost / total_attempts if total_attempts else 0.0
        cost_per_success = total_cost / len(resolved) if resolved else None

    def _rate(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    metrics: dict[str, Any] = {
        "total_tasks": total,
        "total_attempts": total_attempts,
        "infra_failures": len(infra),
        "valid_tasks": len(valid),
        "resolved_tasks": len(resolved),
        "pass_at_1_tasks": len(pass1),
        "timed_out_tasks": len(timed_out),
        # 正确性
        "resolution_rate": _rate(len(resolved), len(valid)),
        "pass_at_1": _rate(len(pass1), total),
        "final_success_rate": _rate(len(resolved), total),
        "regression_pass_rate": _rate(len(p2p_ok), len(code_change)),
        # 稳定性
        "timeout_rate": _rate(len(timed_out), total),
        "infra_error_rate": _rate(len(infra), total),
        # 效率
        "avg_duration_seconds": round(total_duration / total, 2) if total else 0.0,
        "p50_duration_seconds": round(_percentile(durations, 50), 2),
        "p95_duration_seconds": round(_percentile(durations, 95), 2),
        "avg_tokens": round(total_tokens / total, 1) if total else 0.0,
        "total_tokens": total_tokens,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_llm_requests": total_llm_requests,
        "avg_llm_requests_per_task": round(total_llm_requests / total, 1) if total else 0.0,
        "total_tool_calls": total_tool_calls,
        "avg_tool_calls_per_task": round(total_tool_calls / total, 1) if total else 0.0,
        # 成本
        "cost_per_task": cost_per_task,
        "cost_per_attempt": cost_per_attempt,
        "cost_per_success": cost_per_success,
        "total_cost": total_cost,
        # 清单
        "infra_failure_ids": [o.spec.id for o in infra],
        "timed_out_ids": [o.spec.id for o in timed_out],
    }
    return metrics


def _percentile(sorted_vals: list[float], p: float) -> float:
    """线性插值分位数（p ∈ [0, 100]）。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _p2p_failed(attempt) -> bool:
    """该尝试是否在 P2P 上有失败项（用于回归通过率）。"""
    return bool(attempt.grade.p2p_failed)


def category_breakdown(outcomes: list[TaskOutcome]) -> dict[str, dict[str, Any]]:
    """按类别统计 resolved / 总数（含样本数）。"""
    breakdown: dict[str, dict[str, Any]] = {}
    for o in outcomes:
        c = o.spec.category
        b = breakdown.setdefault(c, {"total": 0, "resolved": 0, "valid": 0})
        b["total"] += 1
        if not o.infra_failure:
            b["valid"] += 1
        if o.resolved:
            b["resolved"] += 1
    for b in breakdown.values():
        b["rate"] = round(b["resolved"] / b["valid"], 4) if b["valid"] else 0.0
    return breakdown


def resolved_list(outcomes: list[TaskOutcome]) -> list[dict[str, Any]]:
    """已解决任务的明细清单，用于报告罗列通过情况。"""
    rows: list[dict[str, Any]] = []
    for o in outcomes:
        if o.infra_failure:
            continue
        if not o.resolved:
            continue
        best = o.best_attempt
        f2p = len(best.grade.fail_to_pass_passed) if best else 0
        p2p = len(best.grade.pass_to_pass_passed) if best else 0
        if o.spec.kind == "code_change":
            if f2p or p2p:
                passed = f"F2P {f2p} 通过"
                if p2p:
                    passed += f"，P2P {p2p} 通过"
            else:
                passed = best.grade.detail if best and best.grade.detail else "测试通过"
        elif o.spec.kind == "answer_match":
            passed = "答案匹配"
        elif o.spec.kind == "terminal":
            passed = f"{f2p} 个测试通过"
        else:
            passed = "文件断言通过"
        rows.append(
            {
                "id": o.spec.id,
                "category": o.spec.category,
                "title": o.spec.title,
                "kind": o.spec.kind,
                "passed": passed,
                "attempts": len(o.attempts),
                "pass_at_1": o.first_attempt is not None and o.first_attempt.success,
            }
        )
    return rows


def failures_by_category(outcomes: list[TaskOutcome]) -> list[dict[str, Any]]:
    """未解决任务的失败原因清单，用于报告里归因功能不足。"""
    rows: list[dict[str, Any]] = []
    for o in outcomes:
        if o.infra_failure:
            continue
        if any(a.timed_out for a in o.attempts):
            continue  # 超时任务在报告「超时/预算」小节单独列出
        if o.resolved:
            continue
        best = o.best_attempt
        rows.append(
            {
                "id": o.spec.id,
                "category": o.spec.category,
                "title": o.spec.title,
                "failed_tests": best.grade.failed if best else [],
                "p2p_failed": best.grade.p2p_failed if best else [],
                "detail": best.grade.detail if best else "no attempt",
                "errors": best.errors if best else [],
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 失败分类（长文方案第五节：统一失败分类体系）
# ---------------------------------------------------------------------------


def classify_failure(o: TaskOutcome) -> str:
    """对单个任务做启发式失败分类，返回主要失败类别（8 类之一）。

    依据现有信号：infra / timeout / errors / 重试次数 / P2P 回归 / F2P 失败 / kind。
    这是启发式推断，真实根因需结合 trace.jsonl 与 grader.json 确认。
    """
    if o.infra_failure:
        return "Infrastructure Failure"
    if o.timed_out:
        return "Budget / Timeout"
    best = o.best_attempt
    if best is None:
        return "Verification Failure"
    if best.grade.p2p_failed:
        return "Coding Failure"  # 回归破坏：改坏了原有功能
    if best.errors:
        # 有报错且重试过 → 恢复失败；否则工具失败
        return "Recovery Failure" if len(o.attempts) > 1 else "Tool Failure"
    if best.grade.failed:
        return "Coding Failure"  # F2P 未通过：代码没修对
    if o.spec.kind in ("answer_match", "file_assert"):
        return "Planning Failure"  # 能力探测未命中：多为理解 / 计划偏差
    return "Verification Failure"


def failure_taxonomy(outcomes: list[TaskOutcome]) -> dict[str, list[str]]:
    """汇总失败分类：{类别: [task_id]}，仅统计未解决的非 infra 任务（infra 单列）。"""
    tax: dict[str, list[str]] = {}
    for o in outcomes:
        if o.resolved:
            continue
        cat = classify_failure(o)
        tax.setdefault(cat, []).append(o.spec.id)
    return tax


# ---------------------------------------------------------------------------
# 版本对比（长文方案第四节）
# ---------------------------------------------------------------------------


def compare_runs(
    current: list[TaskOutcome], baseline: list[TaskOutcome]
) -> dict[str, Any]:
    """对比两个版本的逐任务结果，计算 Fixed / Regressed / Unchanged。

    注意：只有两版使用相同任务集、基础代码、模型与评分器时，差异才能归因于
    Agent 代码改动（长文方案明确提醒）。
    """
    base = {o.spec.id: o.resolved for o in baseline}
    fixed: list[str] = []
    regressed: list[str] = []
    unchanged: list[str] = []
    unresolved: list[str] = []
    for o in current:
        cid = o.spec.id
        prev = base.get(cid)
        if prev is None:
            continue  # 基线里没有该题，跳过
        cur = o.resolved
        if prev and cur:
            unchanged.append(cid)
        elif not prev and cur:
            fixed.append(cid)
        elif prev and not cur:
            regressed.append(cid)
        else:
            unresolved.append(cid)
    return {
        "fixed": sorted(fixed),
        "regressed": sorted(regressed),
        "unchanged": sorted(unchanged),
        "unresolved": sorted(unresolved),
        "baseline_size": len(base),
        "current_size": len(current),
        "comparable": len(fixed) + len(regressed) + len(unchanged) + len(unresolved),
    }
