from __future__ import annotations

import time
from typing import TYPE_CHECKING

from kyncode.commands.registry import Command, CommandContext, CommandType

if TYPE_CHECKING:
    from kyncode.agents.trace import TraceManager, TraceNode


def _format_elapsed(start: float, end: float | None) -> str:
    elapsed = (end or time.monotonic()) - start
    if elapsed >= 60:
        return f"{elapsed / 60:.1f}m"
    return f"{elapsed:.0f}s"


def _status_icon(status: str) -> str:
    return {"running": "⏳", "completed": "✓", "failed": "✗"}.get(status, "?")


def _format_node(n: TraceNode, indent: int) -> str:
    icon = _status_icon(n.status)
    elapsed = _format_elapsed(n.start_time, n.end_time)
    tokens = (
        f" ↑{n.input_tokens} ↓{n.output_tokens}"
        if n.input_tokens or n.output_tokens
        else ""
    )
    return (
        f"{'  ' * indent}{icon} [{n.agent_id[:8]}] {n.agent_type}"
        f" — {n.status} ({elapsed}){tokens}"
    )


def _render_tree_lines(nodes: list[TraceNode]) -> list[str]:
    """把一组节点渲染成缩进树文本，根优先。"""
    parent_map: dict[str | None, list[TraceNode]] = {}
    for n in nodes:
        parent_map.setdefault(n.parent_id, []).append(n)

    lines: list[str] = ["Agent 追踪树:"]

    def _render(parent_id: str | None, indent: int) -> None:
        for n in parent_map.get(parent_id, []):
            lines.append(_format_node(n, indent))
            _render(n.agent_id, indent + 1)

    # 根 = parent_id 为空，或父节点不在本组（兼容落盘前旧数据里 lead 未入树）
    ids = {n.agent_id for n in nodes}
    roots = [n for n in nodes if n.parent_id is None or n.parent_id not in ids]
    if not roots:
        roots = nodes[:1]

    for root in roots:
        lines.append(_format_node(root, 0))
        _render(root.agent_id, 1)

    total_in = sum(n.input_tokens for n in nodes)
    total_out = sum(n.output_tokens for n in nodes)
    lines.append(f"\n  合计: {len(nodes)} agents, ↑{total_in} ↓{total_out}")
    return lines


def create_trace_command(trace_manager: TraceManager) -> Command:

    async def handler(ctx: CommandContext) -> None:
        arg = ctx.args.strip()

        # 列出所有落盘追踪
        if arg in ("list", "--all", "-a", "all"):
            summaries = trace_manager.list_traces()
            if not summaries:
                ctx.ui.add_system_message("没有已落盘的 Agent 追踪记录。")
                return
            lines = ["已落盘的 Agent 追踪（按时间倒序）:"]
            for s in summaries:
                ts = (
                    time.strftime("%m-%d %H:%M", time.localtime(s["updated_at"]))
                    if s["updated_at"]
                    else "?"
                )
                lines.append(
                    f"  [{s['trace_id'][:8]}] {s['node_count']} agents, "
                    f"↑{s['total_in']} ↓{s['total_out']} — {ts}"
                )
            lines.append("\n用 /trace <id> 查看某次追踪的完整树")
            ctx.ui.add_system_message("\n".join(lines))
            return

        # 加载某次历史追踪（支持前缀匹配）
        if arg:
            nodes = trace_manager.load(arg)
            if nodes is None:
                matches = [
                    s["trace_id"]
                    for s in trace_manager.list_traces()
                    if s["trace_id"].startswith(arg)
                ]
                if len(matches) == 1:
                    nodes = trace_manager.load(matches[0])
            if nodes is None:
                ctx.ui.add_system_message(f"未找到追踪记录: {arg}")
                return
            lines = _render_tree_lines(nodes)
            ctx.ui.add_system_message("\n".join(lines))
            return

        # 默认：当前内存树
        nodes = list(trace_manager._nodes.values())
        if not nodes:
            ctx.ui.add_system_message(
                "没有 Agent 追踪记录。用 /trace list 查看已落盘的历史追踪。"
            )
            return
        ctx.ui.add_system_message("\n".join(_render_tree_lines(nodes)))

    return Command(
        name="trace",
        description="查看 Agent 父子追踪树（/trace；/trace list 看历史）",
        type=CommandType.LOCAL,
        handler=handler,
        aliases=["tree"],
        usage="/trace [list | <trace_id>]",
    )
