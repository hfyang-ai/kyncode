"""KynCode Evals CLI 入口。

解析参数 → 构建 RunContext → 驱动 Orchestrator 执行 → 汇总指标与报告。

用法：
    python runner.py --mock                # 离线合成数据，跑通全流程出示例报告
    python runner.py --live                # 真实调用 config.yaml 的 agent_bin
    python runner.py --category bugfix     # 只跑某类别
    python runner.py --task bugfix_001     # 只跑某题
    python runner.py --retries 1           # 覆盖默认重试次数
    python runner.py --report-only results/<run_id>   # 从已有结果重算报告
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from orchestrator import Orchestrator, RunContext
from tasklib import (
    Attempt,
    EvalConfig,
    GraderResult,
    TaskOutcome,
    discover_tasks,
    load_json,
    new_run_id,
    save_json,
)

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="KynCode 测评执行器")
    parser.add_argument("--live", action="store_true", help="真实调用 agent_bin")
    parser.add_argument("--mock", action="store_true", help="离线合成数据（默认）")
    parser.add_argument("--category", help="只跑指定类别")
    parser.add_argument("--task", help="只跑指定任务 id")
    parser.add_argument("--retries", type=int, default=None, help="覆盖默认重试次数")
    parser.add_argument("--report-only", help="从已有 results/<run_id> 重算报告")
    parser.add_argument("--compare", help="对比基线 run_id（读取 results/<id>/grades.json）")
    parser.add_argument("--no-llm-report", action="store_true", help="禁用 LLM 报告生成，用规则模板")
    args = parser.parse_args()

    cfg = EvalConfig.load(ROOT / "config.yaml")
    mode = "live" if args.live else "mock"

    # 版本对比基线：加载基线 run 的 grades.json 重建 outcomes
    baseline_outcomes = None
    if args.compare:
        baseline_dir = ROOT / cfg.work_root / args.compare
        if not (baseline_dir / "grades.json").exists():
            print(f"未找到基线 {baseline_dir}/grades.json", file=sys.stderr)
            sys.exit(1)
        baseline_outcomes = _outcomes_from_grades(baseline_dir)

    if args.report_only:
        _recompute_report(
            Path(args.report_only), cfg, baseline_outcomes,
            use_llm=not args.no_llm_report,
        )
        return

    specs = discover_tasks(ROOT / "tasks", ROOT / "datasets")
    if args.category:
        specs = [s for s in specs if s.category == args.category]
    if args.task:
        specs = [s for s in specs if s.id == args.task]
    if args.retries is not None:
        for s in specs:
            s.max_retries = args.retries
    if not specs:
        print("没有匹配的任务", file=sys.stderr)
        sys.exit(1)

    run_id = new_run_id()
    run_dir = ROOT / cfg.work_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    orch = Orchestrator(run_dir, RunContext(run_id, mode, cfg, specs))
    orch.write_manifest()

    print(f"run_id={run_id} mode={mode} tasks={len(specs)} agent={cfg.agent_bin}")

    outcomes: list[TaskOutcome] = []
    for i, spec in enumerate(specs, 1):
        outcome = orch.run_task(spec)
        outcomes.append(outcome)
        print(f"[{i}/{len(specs)}] {spec.id:<16} {outcome.status.value}")

    orch.write_summary(outcomes)

    # 保留 grades.json 便于向后兼容与 --report-only 复算
    _write_grades(run_dir, outcomes)
    _write_report(
        run_dir, run_id, mode, cfg, outcomes, baseline_outcomes,
        use_llm=not args.no_llm_report,
    )


def _write_grades(run_dir: Path, outcomes: list[TaskOutcome]) -> None:
    grades = [
        {
            "id": o.spec.id,
            "category": o.spec.category,
            "resolved": o.resolved,
            "status": o.status.value,
            "infra_failure": o.infra_failure,
            "timed_out": o.timed_out,
            "attempts": [
                {
                    "index": a.index,
                    "success": a.success,
                    "duration_seconds": a.duration_seconds,
                    "input_tokens": a.input_tokens,
                    "output_tokens": a.output_tokens,
                    "num_turns": a.num_turns,
                    "tool_calls_count": len(a.tool_calls),
                    "errors": a.errors,
                    "grade": {
                        "resolved": a.grade.resolved,
                        "failed": a.grade.failed,
                        "p2p_failed": a.grade.p2p_failed,
                        "detail": a.grade.detail,
                    },
                }
                for a in o.attempts
            ],
        }
        for o in outcomes
    ]
    save_json(run_dir / "grades.json", grades)


def _write_report(
    run_dir: Path,
    run_id: str,
    mode: str,
    cfg: EvalConfig,
    outcomes: list[TaskOutcome],
    baseline_outcomes: list[TaskOutcome] | None = None,
    use_llm: bool = True,
) -> None:
    from metrics import compare_runs, compute_metrics

    pricing = getattr(cfg, "pricing", None)
    metrics = compute_metrics(outcomes, pricing)
    save_json(run_dir / "metrics.json", metrics)
    manifest = (
        load_json(run_dir / "manifest.json")
        if (run_dir / "manifest.json").exists()
        else None
    )
    baseline = compare_runs(outcomes, baseline_outcomes) if baseline_outcomes else None
    report, tag = _render_report(
        run_id, mode, cfg, metrics, outcomes, manifest, baseline, use_llm
    )
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"\n{tag}报告: {run_dir / 'report.md'}")


def _recompute_report(
    run_dir: Path,
    cfg: EvalConfig,
    baseline_outcomes: list[TaskOutcome] | None = None,
    use_llm: bool = True,
) -> None:
    if not (run_dir / "grades.json").exists():
        print(f"未找到 {run_dir}/grades.json", file=sys.stderr)
        sys.exit(1)
    outcomes = _outcomes_from_grades(run_dir)
    from metrics import compare_runs, compute_metrics

    metrics = compute_metrics(outcomes, getattr(cfg, "pricing", None))
    manifest = (
        load_json(run_dir / "manifest.json")
        if (run_dir / "manifest.json").exists()
        else None
    )
    baseline = compare_runs(outcomes, baseline_outcomes) if baseline_outcomes else None
    report, tag = _render_report(
        run_dir.name, "recompute", cfg, metrics, outcomes, manifest, baseline, use_llm
    )
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    print(f"{tag}报告已重算: {run_dir / 'report.md'}")


def _render_report(
    run_id: str,
    mode: str,
    cfg: EvalConfig,
    metrics: dict,
    outcomes: list[TaskOutcome],
    manifest: dict | None,
    baseline: dict | None,
    use_llm: bool,
) -> tuple[str, str]:
    """生成报告，返回 (report_md, 来源标签)。LLM 失败时回退规则模板。"""
    if use_llm:
        from report_llm import render_llm_report

        p = Path(cfg.agent_config) if cfg.agent_config else None
        agent_config_path = None
        if p is not None:
            agent_config_path = p if p.is_absolute() else (ROOT / p).resolve()
        report, used_llm = render_llm_report(
            run_id, mode, cfg.agent_bin, metrics, outcomes,
            manifest=manifest, baseline=baseline, agent_config_path=agent_config_path,
        )
        return report, "LLM 生成" if used_llm else "规则模板（LLM 失败回退）"
    from report import render_report

    report = render_report(
        run_id, mode, cfg.agent_bin, metrics, outcomes,
        manifest=manifest, baseline=baseline,
    )
    return report, "规则模板"


def _outcomes_from_grades(run_dir: Path) -> list[TaskOutcome]:
    grades = load_json(run_dir / "grades.json")
    specs = {s.id: s for s in discover_tasks(ROOT / "tasks", ROOT / "datasets")}
    outcomes: list[TaskOutcome] = []
    for g in grades:
        spec = specs.get(g["id"])
        if spec is None:
            continue
        outcome = TaskOutcome(spec=spec)
        for a in g["attempts"]:
            gr = GraderResult(
                resolved=a["grade"]["resolved"],
                failed=a["grade"].get("failed", []),
                p2p_failed=a["grade"].get("p2p_failed", []),
                detail=a["grade"].get("detail", ""),
            )
            outcome.attempts.append(
                Attempt(
                    index=a["index"],
                    task_id=g["id"],
                    success=a["success"],
                    infra_failure=g.get("infra_failure", False),
                    timed_out=g.get("timed_out", False),
                    duration_seconds=a["duration_seconds"],
                    input_tokens=a["input_tokens"],
                    output_tokens=a["output_tokens"],
                    num_turns=a["num_turns"],
                    tool_calls=[
                        {"name": f"call{i}"}
                        for i in range(a.get("tool_calls_count", 0))
                    ],
                    errors=a["errors"],
                    stop_reason="",
                    final_text="",
                    grade=gr,
                )
            )
        outcomes.append(outcome)
    return outcomes


if __name__ == "__main__":
    main()
