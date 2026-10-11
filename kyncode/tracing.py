from __future__ import annotations

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
