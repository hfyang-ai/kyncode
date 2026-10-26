"""Terminal-Bench 评测 CLI：跑任务 → 复用 evals 的 compute_metrics + render_report 出八部分报告。

用法：
    # 跑单个任务（hello-world，验证全链路）
    python terminal_bench_runner.py --tasks hello-world

    # 跑指定任务 / 类别 / 数量
    python terminal_bench_runner.py --category file-operations --n 5

    # 指定任务数据目录与 wheel / agent config
    python terminal_bench_runner.py --tasks-root /tmp/tb_repo/original-tasks \
        --wheel ../dist/kyncode-0.1.0-py3-none-any.whl
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tasklib import Attempt, EvalConfig, GraderResult, TaskOutcome, new_run_id, save_json
from terminal_bench import TerminalBenchRunner, load_terminal_tasks

ROOT = Path(__file__).resolve().parent

DEFAULT_TASKS_ROOT = Path("/tmp/tb_repo/original-tasks")


def main() -> None:
    parser = argparse.ArgumentParser(description="KynCode Terminal-Bench 评测")
    parser.add_argument("--tasks", help="逗号分隔的任务 id 列表")
    parser.add_argument(
        "--category",
        help="只跑指定类别（file-operations / software-engineering / ...）",
    )
    parser.add_argument(
        "--difficulty",
        help="只跑指定难度（easy / medium / hard）",
    )
    parser.add_argument("--n", type=int, help="只跑前 N 个任务")
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="并发跑的任务数（默认 1 串行；建议 4-6）",
    )
    parser.add_argument(
        "--tasks-root", default=str(DEFAULT_TASKS_ROOT), help="Terminal-Bench 任务目录"
    )
    parser.add_argument(
        "--wheel",
        help="kyncode wheel 路径（默认 ../dist/kyncode-0.1.0-py3-none-any.whl）",
    )
    parser.add_argument(
        "--agent-config",
        help="待测 agent config 路径（默认 evals/config.yaml 的 agent_config）",
    )
    parser.add_argument(
        "--no-rebuild", action="store_true", help="复用已构建镜像（不重新 build）"
    )
    parser.add_argument("--cleanup", action="store_true", help="跑完删除镜像")
    parser.add_argument("--list", action="store_true", help="只列出任务，不执行")
    parser.add_argument(
        "--no-llm-report", action="store_true", help="禁用 LLM 报告生成，用规则模板"
    )
    args = parser.parse_args()

    cfg = EvalConfig.load(ROOT / "config.yaml")
    tasks_root = Path(args.tasks_root)
    if not tasks_root.exists():
        print(f"任务目录不存在: {tasks_root}", file=sys.stderr)
        sys.exit(1)

    task_ids = [t.strip() for t in args.tasks.split(",")] if args.tasks else None
    tasks = load_terminal_tasks(
        tasks_root,
        task_ids=task_ids,
        category=args.category,
        difficulty=args.difficulty,
        n=args.n,
    )

    if args.list:
        print(f"共 {len(tasks)} 个任务：")
        for t in tasks:
            print(f"  {t.id:<40} [{t.category}] {t.difficulty}")
        return

    if not tasks:
        print("没有匹配的任务", file=sys.stderr)
        sys.exit(1)

    wheel = (
        Path(args.wheel)
        if args.wheel
        else ROOT.parent / "dist" / "kyncode-0.1.0-py3-none-any.whl"
    )
    if not wheel.exists():
        print(f"wheel 不存在: {wheel}（先在主工程根运行 `uv build`）", file=sys.stderr)
        sys.exit(1)

    agent_config = (
        Path(args.agent_config)
        if args.agent_config
        else (ROOT / cfg.agent_config).resolve()
    )
    if not agent_config.exists():
        print(f"agent config 不存在: {agent_config}", file=sys.stderr)
        sys.exit(1)

    runner = TerminalBenchRunner(
        tasks_root=tasks_root,
        wheel_path=wheel,
        agent_config_path=agent_config,
        no_rebuild=args.no_rebuild,
        cleanup=args.cleanup,
    )

    run_id = new_run_id()
    run_dir = ROOT / cfg.work_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"run_id={run_id} tasks={len(tasks)} wheel={wheel.name} "
        f"agent_config={agent_config.name}"
    )

    outcomes = _run_tasks(runner, tasks, args.parallel)

    # 复用报告链路
    _write_report(
        run_dir, run_id, cfg, outcomes, wheel, agent_config,
        use_llm=not args.no_llm_report,
    )


def _run_tasks(
    runner: TerminalBenchRunner, tasks, parallel: int
) -> list[TaskOutcome]:
    """串行（parallel=1）或并发执行任务，返回按输入顺序排列的 outcomes。"""
    if parallel <= 1:
        outcomes: list[TaskOutcome] = []
        for i, task in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {task.id:<40} ", end="", flush=True)
            outcome = runner.run_one(task)
            outcomes.append(outcome)
            _print_status(outcome)
        return outcomes

    # 并发：as_completed 乱序完成，按 task.id 收集后回到输入顺序
    by_id: dict[str, TaskOutcome] = {}
    done = 0
    total = len(tasks)
    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futs = {ex.submit(runner.run_one, t): t for t in tasks}
        for fut in as_completed(futs):
            task = futs[fut]
            try:
                outcome = fut.result()
            except Exception as e:  # run_one 内部已兜底，此处为最终保险
                outcome = _infra_failure_outcome(runner, task, e)
            by_id[task.id] = outcome
            done += 1
            print(f"[{done}/{total}] {task.id:<40} ", end="", flush=True)
            _print_status(outcome)
    return [by_id[t.id] for t in tasks]


def _print_status(outcome: TaskOutcome) -> None:
    status = outcome.status.value
    extra = ""
    if outcome.resolved:
        extra = f"（{len(outcome.best_attempt.grade.fail_to_pass_passed)} 测试通过）"
    print(f"{status}{extra}")


def _infra_failure_outcome(
    runner: TerminalBenchRunner, task, err: Exception
) -> TaskOutcome:
    spec = runner._make_spec(task)
    attempt = Attempt(
        index=0,
        task_id=task.id,
        success=False,
        infra_failure=True,
        timed_out=False,
        duration_seconds=0.0,
        input_tokens=0,
        output_tokens=0,
        num_turns=0,
        tool_calls=[],
        errors=[str(err)],
        stop_reason="infra",
        final_text="",
        trace=[],
        grade=GraderResult(resolved=False, detail=str(err)),
    )
    return TaskOutcome(spec=spec, attempts=[attempt])


def _write_report(
    run_dir: Path,
    run_id: str,
    cfg: EvalConfig,
    outcomes,
    wheel: Path,
    agent_config_path: Path,
    use_llm: bool = True,
) -> None:
    from metrics import compute_metrics
    from report_llm import render_llm_report

    metrics = compute_metrics(outcomes, getattr(cfg, "pricing", None))
    save_json(run_dir / "metrics.json", metrics)
    save_json(run_dir / "grades.json", _to_grades(outcomes))

    manifest = {
        "agent_bin": "kyncode (containerized)",
        "agent_version": cfg.agent_version,
        "model": cfg.model,
        "dataset": "terminal-bench-core 0.1.1",
        "wheel": wheel.name,
    }
    save_json(run_dir / "manifest.json", manifest)

    if use_llm:
        report, used_llm = render_llm_report(
            run_id,
            "terminal-bench",
            "kyncode (containerized)",
            metrics,
            outcomes,
            manifest=manifest,
            baseline=None,
            agent_config_path=agent_config_path,
        )
        tag = "LLM 生成" if used_llm else "规则模板（LLM 失败回退）"
        print(f"\n{tag}报告: {run_dir / 'report.md'}")
    else:
        from report import render_report

        report = render_report(
            run_id,
            "terminal-bench",
            "kyncode (containerized)",
            metrics,
            outcomes,
            manifest=manifest,
            baseline=None,
        )
        print(f"\n完成（规则模板）。报告: {run_dir / 'report.md'}")

    (run_dir / "report.md").write_text(report, encoding="utf-8")


def _to_grades(outcomes) -> list[dict]:
    grades = []
    for o in outcomes:
        grades.append(
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
        )
    return grades


if __name__ == "__main__":
    main()
