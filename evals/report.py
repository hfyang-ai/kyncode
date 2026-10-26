"""Markdown 测评报告生成：对齐长文方案八部分结构。

结构：实验信息 → 总体摘要 → 核心指标 → 任务分类 → 版本对比 →
失败分析 → 成本效率 → 可靠性有效性 → 改进建议 → 附录（逐任务结果）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from metrics import (
    category_breakdown,
    failure_taxonomy,
    failures_by_category,
)
from tasklib import TaskOutcome

_CN_NUM = "零一二三四五六七八九十"


def _cn(n: int) -> str:
    """章节序号转中文数字，超过 10 直接用阿拉伯数字。"""
    return _CN_NUM[n] if n <= 10 else str(n)


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _fmt_cost(x: float | None) -> str:
    return f"${x:.4f}" if x is not None else "未配置价格"


def _fmt_duration(s: float) -> str:
    return f"{s:.1f}s"


def render_report(
    run_id: str,
    mode: str,
    agent_bin: str,
    metrics: dict[str, Any],
    outcomes: list[TaskOutcome],
    manifest: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    add = lines.append
    sec = 0

    def section(title: str) -> None:
        nonlocal sec
        sec += 1
        add(f"## {_cn(sec)}、{title}")
        add("")

    add("# KynCode 测评报告")
    add("")

    _experiment_info(add, run_id, mode, agent_bin, metrics, outcomes, manifest)

    # 一、总体摘要
    section("总体摘要")
    add(_executive_summary(metrics, outcomes, agent_bin))
    add("")

    # 二、核心指标
    section("核心指标")
    add(_core_metrics(metrics))
    add("")

    # 三、任务分类分析
    section("任务分类分析")
    add(_task_breakdown(outcomes))
    add("")

    # 四、版本对比（可选）
    if baseline:
        section("版本对比")
        add(_comparison(baseline))
        add("")

    # 五、失败分析
    section("失败分析")
    add(_failure_analysis(outcomes))
    add("")

    # 六、成本与效率
    section("成本与效率")
    add(_cost_efficiency(metrics))
    add("")

    # 七、可靠性与评测有效性
    section("可靠性与评测有效性")
    add(_reliability(metrics, outcomes, manifest))
    add("")

    # 八、改进建议
    section("改进建议")
    add(_action_items(metrics, outcomes))
    add("")

    # 附录
    add("## 附录：逐任务结果")
    add("")
    add(_appendix(outcomes))
    add("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 头部：实验信息（长文第九节「实验信息」）
# ---------------------------------------------------------------------------


def _experiment_info(
    add,
    run_id: str,
    mode: str,
    agent_bin: str,
    metrics: dict[str, Any],
    outcomes: list[TaskOutcome],
    manifest: dict[str, Any] | None,
) -> None:
    m = manifest or {}
    agent_version = m.get("agent_version") or "未标注"
    model = m.get("model") or "未标注"

    # 数据集版本：从任务里聚合去重
    versions = sorted({o.spec.dataset_version for o in outcomes if o.spec.dataset_version})
    dataset_version = ", ".join(versions) if versions else "未版本化"

    add("## 实验信息")
    add("")
    add("| 项 | 值 |")
    add("| --- | --- |")
    add(f"| Run ID | `{run_id}` |")
    add(f"| 被测 Agent | `{agent_bin}` |")
    add(f"| Agent 版本 | `{agent_version}` |")
    add(f"| 模型 | `{model}` |")
    add(f"| 执行模式 | {mode} |")
    add(f"| 数据集版本 | {dataset_version} |")
    add(f"| 任务总数 | {metrics['total_tasks']} |")
    add(f"| 生成时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |")
    add("")


# ---------------------------------------------------------------------------
# 一、总体摘要
# ---------------------------------------------------------------------------


def _executive_summary(
    metrics: dict[str, Any], outcomes: list[TaskOutcome], agent_bin: str
) -> str:
    lines: list[str] = []
    add = lines.append

    rate = metrics["resolution_rate"]
    if rate >= 0.8:
        verdict = "整体表现良好，核心能力基本达标"
    elif rate >= 0.5:
        verdict = "整体表现一般，存在明显短板需要针对性加固"
    else:
        verdict = "整体表现偏弱，需系统性优化"

    resolved = metrics["resolved_tasks"]
    valid = metrics["valid_tasks"]
    add(
        f"本次测评在 {metrics['total_tasks']} 道任务上对 `{agent_bin}` "
        f"进行了评估，任务解决率 **{_fmt_pct(rate)}**（{resolved}/{valid} 有效任务）。"
        f"总体结论：**{verdict}**。"
    )
    add("")

    # 关键数字面板
    cost = metrics.get("cost_per_task")
    add("| 任务解决率 | 回归通过率 | P50 耗时 | 单任务成本 |")
    add("| --- | --- | --- | --- |")
    add(
        f"| **{_fmt_pct(rate)}** ({resolved}/{valid}) "
        f"| **{_fmt_pct(metrics['regression_pass_rate'])}** "
        f"| **{_fmt_duration(metrics['p50_duration_seconds'])}** "
        f"| **{_fmt_cost(cost)}** |"
    )
    add("")

    # 主要发现
    add("**主要发现**：")
    add("")
    bd = category_breakdown(outcomes)
    if bd:
        weakest = min(bd.items(), key=lambda kv: kv[1]["rate"])
        if weakest[1]["rate"] < 0.8:
            add(
                f"- 分类别表现最弱的是 **{weakest[0]}**：解决率 "
                f"{_fmt_pct(weakest[1]['rate'])}（{weakest[1]['resolved']}/{weakest[1]['valid']}），是优先优化方向。"
            )
        else:
            add(f"- 各类别表现均衡良好（最低解决率 {_fmt_pct(weakest[1]['rate'])}）。")
    tax = failure_taxonomy(outcomes)
    if tax:
        top_cat = max(tax.items(), key=lambda kv: len(kv[1]))
        add(f"- 失败最集中的原因是 **{top_cat[0]}**（{len(top_cat[1])} 题），详见「失败分析」。")

    # 主要风险
    add("")
    add("**主要风险**：")
    add("")
    p2p_broken = [f["id"] for f in failures_by_category(outcomes) if f["p2p_failed"]]
    if p2p_broken:
        add(f"- 存在**回归破坏**：`{', '.join(p2p_broken[:5])}` 在修改过程中改坏了原有功能。")
    if metrics["infra_failures"]:
        add(f"- {metrics['infra_failures']} 道题因基础设施故障未计入能力统计，需复核环境稳定性。")
    if metrics["timed_out_tasks"]:
        add(f"- {metrics['timed_out_tasks']} 道题超时，长尾耗时（P95）需关注。")
    if not p2p_broken and not metrics["infra_failures"] and not metrics["timed_out_tasks"]:
        add("- 本次未见回归破坏、基础设施故障或超时等显著风险。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 二、核心指标（正确性 / 效率 / 稳定性三组）
# ---------------------------------------------------------------------------


def _core_metrics(m: dict[str, Any]) -> str:
    lines: list[str] = []

    lines.append("**正确性**：")
    lines.append("")
    lines.append("| 指标 | 实测值 | 说明 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 任务解决率 | {_fmt_pct(m['resolution_rate'])} | 解决任务 ÷ 有效任务 |")
    lines.append(f"| Pass@1 | {_fmt_pct(m['pass_at_1'])} | 单次尝试成功任务 ÷ 任务总数 |")
    lines.append(f"| 多次尝试成功率 | {_fmt_pct(m['final_success_rate'])} | 含重试的最终成功率 |")
    lines.append(f"| 回归通过率 | {_fmt_pct(m['regression_pass_rate'])} | P2P 回归测试通过率 |")
    lines.append("")

    lines.append("**效率**：")
    lines.append("")
    lines.append("| 指标 | 实测值 | 说明 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 平均耗时 | {_fmt_duration(m['avg_duration_seconds'])} | 所有任务耗时均值 |")
    lines.append(f"| P50 耗时 | {_fmt_duration(m['p50_duration_seconds'])} | 单任务中位数（更能反映典型体验） |")
    lines.append(f"| P95 耗时 | {_fmt_duration(m['p95_duration_seconds'])} | 长尾任务（揭示慢任务） |")
    lines.append(f"| 平均 Token | {m['avg_tokens']:.0f} | 总 Token ÷ 任务数 |")
    lines.append(f"| 平均 LLM 请求 | {m['avg_llm_requests_per_task']:.1f} | 每任务 agent 轮次（近似请求数） |")
    lines.append(f"| 平均工具调用 | {m['avg_tool_calls_per_task']:.1f} | 每任务工具调用次数 |")
    lines.append(f"| 单任务成本 | {_fmt_cost(m['cost_per_task'])} | 全部有效运行的成本均值 |")
    lines.append("")

    lines.append("**稳定性**：")
    lines.append("")
    lines.append("| 指标 | 实测值 | 说明 |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| 超时率 | {_fmt_pct(m['timeout_rate'])} | 超时任务 ÷ 任务总数 |")
    lines.append(f"| 基础设施错误率 | {_fmt_pct(m['infra_error_rate'])} | 基础设施故障 ÷ 任务总数 |")
    lines.append("")

    lines.append(
        f"实测统计：总任务 **{m['total_tasks']}** / 有效任务 **{m['valid_tasks']}** / "
        f"解决 **{m['resolved_tasks']}** / 基础设施故障 **{m['infra_failures']}** / "
        f"超时 **{m['timed_out_tasks']}**。"
    )
    lines.append(
        f"Token 开销：总 **{m['total_tokens']}**（输入 {m['total_input_tokens']} / "
        f"输出 {m['total_output_tokens']}）；LLM 请求共 {m['total_llm_requests']} 次，"
        f"工具调用共 {m['total_tool_calls']} 次。"
    )
    lines.append("")
    lines.append(
        "> 口径说明：任务解决率 / 回归通过率的分母为**有效任务数**（总任务数 - 基础设施故障任务数）；"
        "Pass@1 与多次尝试成功率分开报告，重试后的成功不计入 Pass@1；基础设施故障与超时单独统计。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 三、任务分类分析
# ---------------------------------------------------------------------------


def _task_breakdown(outcomes: list[TaskOutcome]) -> str:
    bd = category_breakdown(outcomes)
    order = ["basic", "bugfix", "tool_error", "long_context", "refactor"]
    # 追加 order 之外的类别（如 Terminal-Bench 的 file-operations 等），保证都进表格
    ordered = order + [c for c in bd if c not in order]
    lines = ["| 类别 | 样本数 | 解决数 | 解决率 |", "| --- | --- | --- | --- |"]
    for c in ordered:
        if c not in bd:
            continue
        b = bd[c]
        lines.append(f"| {c} | {b['valid']}/{b['total']} | {b['resolved']} | {_fmt_pct(b['rate'])} |")
    lines.append("")

    # 排查方向提示（启发式，非因果结论）
    notes: list[str] = []
    hints = {
        "basic": "基础工具与执行流程已相对成熟，可维持现状",
        "bugfix": "检查代码理解、单点修复与验证闭环",
        "tool_error": "检查异常分类、重试与错误反馈机制",
        "long_context": "检查上下文压缩、历史保留与关键信息丢失",
        "refactor": "检查跨文件依赖理解与改动规划",
    }
    for c, b in bd.items():
        if b["rate"] < 0.6 and b["valid"] >= 3:
            notes.append(f"- **{c}** 较弱（{_fmt_pct(b['rate'])}），{hints.get(c, '建议排查')}。")
    if notes:
        lines.append("> 排查方向（非因果结论，需结合失败轨迹验证）：")
        lines.extend(notes)
        lines.append("")
    else:
        lines.append("> 各类别表现均衡，无明显短板类别。")
        lines.append("")

    lines.append("> 样本数以 `N/M` 展示（N = 有效任务，M = 该类别总题数）；样本过少时比例的参考价值有限。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 四、版本对比
# ---------------------------------------------------------------------------


def _comparison(baseline: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append
    fixed = baseline["fixed"]
    regressed = baseline["regressed"]
    unchanged = baseline["unchanged"]
    unresolved = baseline["unresolved"]

    add(
        f"对比基线：{baseline['baseline_size']} 道题 vs 当前 {baseline['current_size']} 道题，"
        f"可比任务 {baseline['comparable']} 道。"
    )
    add("")
    add("| 变化 | 数量 |")
    add("| --- | --- |")
    add(f"| Fixed（失败 → 成功） | {len(fixed)} |")
    add(f"| Regressed（成功 → 失败） | {len(regressed)} |")
    add(f"| Unchanged（结果不变） | {len(unchanged)} |")
    add(f"| Unresolved（两版均失败） | {len(unresolved)} |")
    add("")

    # 任务级差异明细
    rows: list[tuple[str, str, str]] = []
    for tid in fixed:
        rows.append((tid, "失败", "成功"))
    for tid in regressed:
        rows.append((tid, "成功", "失败"))
    if rows:
        add("| 任务 | 基线 | 当前 | 变化 |")
        add("| --- | --- | --- | --- |")
        for tid, prev, cur in rows:
            tag = "改善" if cur == "成功" else "回归"
            add(f"| `{tid}` | {prev} | {cur} | {tag} |")
        add("")

    if regressed:
        add(
            f"> ⚠️ 存在 **{len(regressed)}** 道回归：`{', '.join(regressed[:8])}`。"
            f"需结合失败轨迹分析版本差异，确认是否由本次改动引入。"
        )
        add("")
    add(
        "> 只有当两版使用相同任务集、基础代码、模型与评分器时，以上差异才能归因于 Agent 代码改动。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 五、失败分析（分类体系 + 代表案例）
# ---------------------------------------------------------------------------


def _failure_analysis(outcomes: list[TaskOutcome]) -> str:
    lines: list[str] = []
    add = lines.append

    tax = failure_taxonomy(outcomes)
    if not tax:
        add("本次测评没有未解决任务（有效任务全部解决）。")
        return "\n".join(lines)

    add("**失败分类汇总**（启发式推断，真实根因需结合 trace.jsonl / grader.json）：")
    add("")
    add("| 失败类型 | 任务数 | 任务 |")
    add("| --- | --- | --- |")
    for cat, ids in sorted(tax.items(), key=lambda kv: -len(kv[1])):
        add(f"| {cat} | {len(ids)} | {', '.join(f'`{i}`' for i in ids[:6])} |")
    add("")

    # 代表性失败案例（每类挑一个，附证据）
    add("**代表性失败案例**：")
    add("")
    fail_map = {o.spec.id: o for o in outcomes if not o.resolved}
    cases = 0
    for cat, ids in sorted(tax.items(), key=lambda kv: -len(kv[1])):
        if cases >= 5:
            break
        picked = next((i for i in ids if i in fail_map), None)
        if picked is None:
            continue
        o = fail_map[picked]
        best = o.best_attempt
        add(f"**`{picked}`** — {cat}")
        add("")
        add(f"- 类别：`{o.spec.category}` · {o.spec.title}")
        if best and best.grade.detail:
            detail = best.grade.detail.replace("|", "\\|")
            add(f"- 直接证据：{detail}")
        if best and best.grade.failed:
            add(f"- 失败测试：{', '.join(best.grade.failed[:4])}")
        if best and best.errors:
            add(f"- 报错信息：{'; '.join(best.errors[:3])}")
        add(f"- 尝试次数：{len(o.attempts)}")
        add("")
        cases += 1
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 六、成本与效率
# ---------------------------------------------------------------------------


def _cost_efficiency(m: dict[str, Any]) -> str:
    lines: list[str] = []
    add = lines.append

    add("| 维度 | 指标 | 实测值 |")
    add("| --- | --- | --- |")
    add(f"| 耗时 | P50 / P95 | {_fmt_duration(m['p50_duration_seconds'])} / {_fmt_duration(m['p95_duration_seconds'])} |")
    add(f"| 耗时 | 平均 | {_fmt_duration(m['avg_duration_seconds'])} |")
    add(f"| Token | 输入 / 输出 | {m['total_input_tokens']} / {m['total_output_tokens']} |")
    add(f"| 请求 | LLM 请求总数 | {m['total_llm_requests']} |")
    add(f"| 工具 | 工具调用总数 | {m['total_tool_calls']} |")
    add("")
    add("**成本**：")
    add("")
    add("| 口径 | 金额 |")
    add("| --- | --- |")
    add(f"| 每次尝试成本 | {_fmt_cost(m['cost_per_attempt'])} |")
    add(f"| 每个成功任务成本 | {_fmt_cost(m['cost_per_success'])} |")
    add(f"| 单任务成本（全部有效运行） | {_fmt_cost(m['cost_per_task'])} |")
    add(f"| 总成本 | {_fmt_cost(m['total_cost'])} |")
    add("")
    if m["cost_per_success"] is None:
        add("> 未配置 `pricing`（config.yaml 的 `input_per_1m` / `output_per_1m`），成本显示为「未配置价格」。")
        add("")
    add(
        "> 关注「每个成功任务成本」而非只看单任务成本：失败任务较多时，后者会低估真实交付成本。"
        "成功率提升是否值得额外成本，需结合任务重要性与延迟要求权衡。"
    )
    add("")
    add("> 逐任务的耗时 / Token / 请求 / 工具调用明细见「附录：逐任务结果」。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 七、可靠性与评测有效性
# ---------------------------------------------------------------------------


def _reliability(
    metrics: dict[str, Any],
    outcomes: list[TaskOutcome],
    manifest: dict[str, Any] | None,
) -> str:
    lines: list[str] = []
    add = lines.append
    m = manifest or {}

    # 状态数量分布
    from tasklib import TaskStatus

    agent_errors = sum(1 for o in outcomes if o.status == TaskStatus.AGENT_ERROR)
    ability_failures = (
        metrics["total_tasks"]
        - metrics["resolved_tasks"]
        - metrics["infra_failures"]
        - metrics["timed_out_tasks"]
    )
    add("**本次测评边界**：")
    add("")
    add(f"- 样本量：**{metrics['total_tasks']}** 道任务（自建测评集）。样本有限，几个百分点的变化不宜直接解读为显著提升。")
    add(f"- 结果分布：成功 {metrics['resolved_tasks']} / 能力失败 {ability_failures} / "
        f"基础设施异常 {metrics['infra_failures']} / 超时 {metrics['timed_out_tasks']} / Agent 异常终止 {agent_errors}。")
    add("- 评分器：分层判分（执行有效性 → 修改约束 → 功能正确性 F2P/P2P），与 Agent 解耦、独立判分。")
    add("- 隐藏测试：F2P（FAIL_TO_PASS）与 P2P（PASS_TO_PASS）按文件名前缀 `test_f2p_*` / `test_p2p_*` 分组，Agent 不可见。")
    add("")
    add("**覆盖范围**：")
    add("")
    add("- 已评估：文件读取、跨文件修改、Bug 修复、测试失败处理、工具错误恢复、长上下文、重构。")
    add("- 未评估：子 agent 委派、多 agent team、权限与钩子、checkpoint 恢复、LLM 降级 failover、真实仓库（SWE-bench）。")
    add("")
    if not m.get("agent_version"):
        add("> ⚠️ 未标注 Agent 版本（config.yaml 的 `agent_version` 为空），不同运行之间无法可靠对比。")
        add("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 八、改进建议（发现 → 动作 → 验收方式）
# ---------------------------------------------------------------------------


def _action_items(metrics: dict[str, Any], outcomes: list[TaskOutcome]) -> str:
    rows: list[tuple[str, str, str]] = []

    bd = category_breakdown(outcomes)
    if bd:
        weakest = min(bd.items(), key=lambda kv: kv[1]["rate"])
        if weakest[1]["rate"] < 0.8:
            rows.append(
                (
                    f"{weakest[0]} 类任务解决率偏低（{_fmt_pct(weakest[1]['rate'])}）",
                    "针对该类任务优化 system prompt / 工具集 / 上下文策略",
                    f"{weakest[0]} 类任务解决率提升且不引入回归",
                )
            )

    tax = failure_taxonomy(outcomes)
    top_fail = None
    if tax:
        top_fail = max(tax.items(), key=lambda kv: len(kv[1]))
        if top_fail[0] == "Recovery Failure":
            rows.append(
                ("工具失败后重复调用、未调整策略", "增加错误分类与重试终止条件", "工具恢复任务成功率提升"),
            )
        elif top_fail[0] == "Tool Failure":
            rows.append(
                ("工具调用参数/命令失败", "检查工具错误的结构化返回与反馈", "工具错误率下降"),
            )
        elif top_fail[0] == "Coding Failure":
            rows.append(
                ("代码实现或回归破坏", "加强修改前规划与回归自检", "F2P/P2P 通过率提升"),
            )
        elif top_fail[0] == "Context Failure":
            rows.append(
                ("关键上下文丢失", "优化上下文保留与压缩策略", "长上下文任务成功率提升"),
            )

    p2p_broken = [f["id"] for f in failures_by_category(outcomes) if f["p2p_failed"]]
    if p2p_broken:
        rows.append(
            (f"出现回归破坏（{len(p2p_broken)} 题）", "分析版本差异与失败轨迹", "回归任务重新通过"),
        )

    if metrics["p95_duration_seconds"] > metrics["p50_duration_seconds"] * 3:
        rows.append(
            ("P95 耗时显著高于 P50（长尾）", "排查慢工具与无效循环", "P95 下降且成功率不退化"),
        )

    if not rows:
        rows.append(("本次未见明显短板", "维持现状，持续监控", "后续测评结果稳定"))

    lines = ["| 发现 | 改进动作 | 验收方式 |", "| --- | --- | --- |"]
    for f, a, v in rows:
        lines.append(f"| {f} | {a} | {v} |")
    lines.append("")
    lines.append("> 每项建议建议指定优先级与复测任务集，形成「发现问题 → 改进实现 → 回归验证」闭环。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 附录：逐任务结果
# ---------------------------------------------------------------------------


def _appendix(outcomes: list[TaskOutcome]) -> str:
    lines = ["| 任务 | 类别 | 状态 | 耗时 | Token | LLM 请求 | 工具调用 | 尝试 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for o in outcomes:
        dur = sum(a.duration_seconds for a in o.attempts)
        tok = sum(a.total_tokens for a in o.attempts)
        req = sum(a.num_turns for a in o.attempts)
        tools = sum(len(a.tool_calls) for a in o.attempts)
        lines.append(
            f"| `{o.spec.id}` | {o.spec.category} | {o.status.value} | "
            f"{dur:.1f}s | {tok} | {req} | {tools} | {len(o.attempts)} |"
        )
    return "\n".join(lines)
