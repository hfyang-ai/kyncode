"""LLM 报告生成：把结构化测评数据传给大模型，生成最终报告。

与 ``report.py``（规则模板）互补：默认走 LLM（更深入、可追踪的失败分析），
LLM 不可用/失败时 fallback 到规则报告。

数据流：``TaskOutcome`` + 执行轨迹 → ``build_report_payload`` 组装结构化 JSON →
``generate_report`` 调 LLM → 返回 Markdown 报告。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from metrics import category_breakdown, classify_failure
from tasklib import TaskOutcome

# 统一失败分类体系（8 类 + 典型表现），既用于 prompt 也用于结构化数据里的参考表。
FAILURE_REFERENCE: list[tuple[str, str]] = [
    ("Planning Failure", "目标理解错误、计划遗漏"),
    ("Context Failure", "丢失关键代码或历史信息"),
    ("Tool Failure", "参数错误、命令失败、工具结果解析失败"),
    ("Recovery Failure", "遇到错误后重复失败或未调整策略"),
    ("Coding Failure", "代码实现错误"),
    ("Verification Failure", "没有测试、测试不充分、误判成功"),
    ("Budget / Timeout", "超时、Token 或轮次预算耗尽"),
    ("Infrastructure Failure", "环境、依赖、网络或评测器异常"),
]

SYSTEM_PROMPT = """\
你是一名资深的 AI Agent 评测分析专家。你的任务是根据结构化测评数据，生成一份严谨、可追溯、可执行的测评报告。

## 报告结构（严格按以下 10 部分组织，用 Markdown）
1. 实验信息：Agent 版本、模型、数据集、运行配置。
2. 执行摘要：总体结论、主要收益、主要风险。
3. 核心指标：成功率、耗时、成本、稳定性。
4. 任务分类分析：不同任务类型的成功率与样本数。
5. 版本对比：Fixed、Regressed、Unchanged（若数据未提供则省略本部分）。
6. 失败分析：失败分类、代表案例、日志证据。
7. 成本效率：Token、工具调用、延迟和单次成功成本。
8. 可靠性与有效性：异常、样本限制、评分器说明。
9. 后续行动：可验证的改进项和验收条件。
10. 附录：逐任务结果。

## 失败分析（报告最有价值的部分，务必详尽）
每个失败任务不要只写「Failed」，必须生成一条可追踪的失败记录，至少包含：
- **Task ID**
- **失败分类**：从下面 8 类中选一个「主要类别」，可附加多个「次要标签」
- **直接证据**：从执行轨迹（工具调用与返回）、测试日志、错误信息中提取具体证据（引用关键轮次、命令、退出码），不要只凭最终结果推断
- **根因假设**：基于证据给出合理假设
- **后续行动**：针对根因的具体改进

### 统一失败分类体系
1. Planning Failure —— 目标理解错误、计划遗漏
2. Context Failure —— 丢失关键代码或历史信息
3. Tool Failure —— 参数错误、命令失败、工具结果解析失败
4. Recovery Failure —— 遇到错误后重复失败或未调整策略
5. Coding Failure —— 代码实现错误
6. Verification Failure —— 没有测试、测试不充分、误判成功
7. Budget / Timeout —— 超时、Token 或轮次预算耗尽
8. Infrastructure Failure —— 环境、依赖、网络或评测器异常

一个任务可有 1 个主要类别 + 多个次要标签。在失败分析末尾给出「失败分类汇总表」，回答：究竟哪种问题导致了最多的任务失败？

注意：数据里会提供启发式分类（heuristic_failure_category）作为参考，但你要根据证据独立判断，不要盲从。

## 后续行动（改进建议）
不要把建议写成空泛的「继续优化 Agent」。每项行动要可验证，用表格呈现，至少含「发现 / 改进动作 / 验收方式」三列，并尽量补齐「负责人 / 优先级 / 目标指标 / 复测任务集」。对于个人项目，负责人可写「本人」。重点是形成「发现问题 → 改进实现 → 回归验证」的闭环。

## 写作要求
- 用简体中文。
- 严格基于给定数据，不要编造数据里没有的事实；证据不足时明确说明「证据不足」。
- 输出纯 Markdown 正文，不要用代码块包裹整个报告，不要加任何解释性前言或后记。
"""


def load_provider(config_path: Path | None) -> dict[str, Any] | None:
    """从 agent config 里读第一个 openai-compat / openai 协议的 provider。

    返回 ``{"base_url", "api_key", "model"}``；找不到返回 None。
    """
    if config_path is None or not config_path.exists():
        return None
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    providers = raw.get("providers") or []
    for p in providers:
        if not isinstance(p, dict):
            continue
        if p.get("protocol") in ("openai-compat", "openai"):
            if p.get("base_url") and p.get("api_key") and p.get("model"):
                return {
                    "base_url": str(p["base_url"]),
                    "api_key": str(p["api_key"]),
                    "model": str(p["model"]),
                }
    return None


def build_report_payload(
    run_id: str,
    mode: str,
    agent_bin: str,
    metrics: dict[str, Any],
    outcomes: list[TaskOutcome],
    manifest: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装传给 LLM 的结构化测评数据。

    失败任务附带完整（截断后）执行轨迹，成功任务仅保留摘要以控制 token。
    """
    m = manifest or {}
    tasks: list[dict[str, Any]] = []
    for o in outcomes:
        best = o.best_attempt
        is_failed = (not o.resolved) and (not o.infra_failure)
        trace: list[dict[str, Any]] = []
        if is_failed and best is not None:
            # 失败通常发生在最后几轮，保留末尾 30 个工具调用足够定位
            trace = best.trace[-30:]

        heuristic = None
        if not o.resolved:
            heuristic = classify_failure(o)

        tasks.append(
            {
                "id": o.spec.id,
                "category": o.spec.category,
                "kind": o.spec.kind,
                "difficulty": o.spec.difficulty,
                "prompt": o.spec.prompt[:1500],
                "status": o.status.value,
                "resolved": o.resolved,
                "infra_failure": o.infra_failure,
                "timed_out": o.timed_out,
                "attempts": len(o.attempts),
                "duration_seconds": round(
                    sum(a.duration_seconds for a in o.attempts), 2
                ),
                "input_tokens": sum(a.input_tokens for a in o.attempts),
                "output_tokens": sum(a.output_tokens for a in o.attempts),
                "num_turns": sum(a.num_turns for a in o.attempts),
                "tool_calls_count": sum(len(a.tool_calls) for a in o.attempts),
                "heuristic_failure_category": heuristic,
                "failed_tests": best.grade.failed if best else [],
                "p2p_failed": best.grade.p2p_failed if best else [],
                "grade_detail": (best.grade.detail[:1500] if best else ""),
                "errors": best.errors if best else [],
                "stop_reason": best.stop_reason if best else "",
                "final_text": (best.final_text[:800] if best else ""),
                "trace": trace,
            }
        )

    return {
        "experiment": {
            "run_id": run_id,
            "agent_bin": agent_bin,
            "agent_version": m.get("agent_version") or "未标注",
            "model": m.get("model") or "未标注",
            "mode": mode,
            "dataset": m.get("dataset", ""),
            "total_tasks": metrics.get("total_tasks", 0),
        },
        "metrics": metrics,
        "category_breakdown": category_breakdown(outcomes),
        "failure_categories_reference": [
            {"name": name, "description": desc} for name, desc in FAILURE_REFERENCE
        ],
        "baseline": baseline,
        "tasks": tasks,
    }


def _clean_markdown(text: str) -> str:
    """去掉 LLM 输出外层可能的 ```markdown ... ``` 包裹。"""
    t = text.strip()
    if t.startswith("```"):
        # 去掉首行 ```markdown / ``` 与末尾 ```
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def generate_report(payload: dict[str, Any], provider: dict[str, Any]) -> str:
    """调 LLM 生成 Markdown 报告。"""
    from openai import OpenAI

    client = OpenAI(base_url=provider["base_url"], api_key=provider["api_key"])
    resp = client.chat.completions.create(
        model=provider["model"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
            },
        ],
        temperature=0.3,
        max_tokens=16384,
        timeout=300.0,
    )
    content = resp.choices[0].message.content or ""
    return _clean_markdown(content)


def render_llm_report(
    run_id: str,
    mode: str,
    agent_bin: str,
    metrics: dict[str, Any],
    outcomes: list[TaskOutcome],
    manifest: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    agent_config_path: Path | None = None,
) -> tuple[str, bool]:
    """LLM 报告生成主入口。返回 ``(report_md, used_llm)``。

    LLM 不可用 / 调用失败时 fallback 到规则报告（``report.render_report``），
    ``used_llm=False`` 表示用了规则报告。
    """
    from report import render_report

    provider = load_provider(agent_config_path)
    if provider is None:
        return (
            render_report(
                run_id, mode, agent_bin, metrics, outcomes,
                manifest=manifest, baseline=baseline,
            ),
            False,
        )
    try:
        payload = build_report_payload(
            run_id, mode, agent_bin, metrics, outcomes,
            manifest=manifest, baseline=baseline,
        )
        md = generate_report(payload, provider)
        if not md.strip():
            raise RuntimeError("LLM 返回空报告")
        return md, True
    except Exception:
        return (
            render_report(
                run_id, mode, agent_bin, metrics, outcomes,
                manifest=manifest, baseline=baseline,
            ),
            False,
        )
