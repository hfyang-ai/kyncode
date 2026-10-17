from __future__ import annotations

import contextvars

# 当前正在执行的 agent 的 id，作为贯穿整条工具调用链路的关联标识。
# agent.run 入口 set、结束 reset；runtime / checker 写 trace 时读取，让
# tool_call / permission_check 能归属到具体 agent——多 agent 并发写同一份
# debug.log 时，没有它就无法区分是谁的动作。非 agent 上下文（一次性调用、
# 测试）时为 None，落到日志里是 null，可接受。
current_agent_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_agent_id", default=None
)

# 内容 trace 的截断上限。这些内容记到 debug.log（root level 为 INFO），
# 全量输出会把日志文件撑爆，超长只保留前缀并标注截断量。
TRACE_ARGS_LIMIT = 2000
TRACE_OUTPUT_LIMIT = 4000
TRACE_TEXT_LIMIT = 8000
TRACE_THINKING_LIMIT = 2000


def truncate(text: str, limit: int) -> str:
    """超长文本只留前缀，末尾标注被截掉的字符数。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[trace truncated {len(text) - limit} chars]"
