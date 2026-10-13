from __future__ import annotations

from dataclasses import replace
from typing import Callable

from kyncode.conversation import Message, ToolResultBlock

# Anthropic 要求每个 tool_use 都有配对的 tool_result，缺一个整条请求就会被拒。
# 会话历史出现不配对的情况有几种来路：用户中断在工具执行途中、进程退出后从磁盘
# 恢复会话、并发写入交错。这里在发请求前统一补齐，各前端不必各写一遍。

# 用于补上没有结果的工具调用。工具可能压根没启动，也可能跑到一半被打断，
# 所以措辞上不能断言它没有产生任何副作用。
INTERRUPTED_TOOL_RESULT = (
    "Tool execution was interrupted. The tool may or may not have completed; "
    "verify before relying on its effects."
)

# 用于用户明确拒绝授权的工具调用。这种情况可以断言什么都没改，必须讲清楚，
# 否则模型会以为修改已经生效并继续往下走。
REJECTED_TOOL_RESULT = (
    "The user rejected this tool use. Nothing was changed "
    "(for file edits, the new content was NOT written)."
)

# 接在拒绝结果后面，引出用户拒绝时输入的话
_REJECTED_WITH_FEEDBACK_PREFIX = " To tell you how to proceed, the user said:\n"


def rejected_tool_result(feedback: str = "") -> str:
    """生成用户拒绝授权时交给模型的工具结果。

    用户顺带输入了话就原样附上，模型据此调整做法；没输入就只返回拒绝说明。
    """
    text = (feedback or "").strip()
    if not text:
        return REJECTED_TOOL_RESULT
    return REJECTED_TOOL_RESULT + _REJECTED_WITH_FEEDBACK_PREFIX + text


def ensure_tool_pairing(messages: list[Message]) -> list[Message]:
    """返回一份修好配对关系的消息副本，输入不会被修改。

    做两件事：给没有结果的 tool_use 补一条标记为错误的 tool_result（紧跟其后），
    丢掉找不到对应 tool_use 的孤儿 tool_result。补出来的内容不写回对话历史：
    历史应当如实记录发生过什么，补位只是为了让这一次请求合法。
    """
    resolved: set[str] = set()
    issued: set[str] = set()
    for m in messages:
        for tr in m.tool_results or []:
            resolved.add(tr.tool_use_id)
        for tu in m.tool_uses or []:
            issued.add(tu.tool_use_id)

    out: list[Message] = []
    for m in messages:
        current = m
        if m.tool_results:
            kept = [tr for tr in m.tool_results if tr.tool_use_id in issued]
            if not kept and not m.content and not m.tool_uses:
                continue  # 整条消息只剩空壳，丢掉以免破坏角色交替
            current = replace(m, tool_results=kept)
        out.append(current)

        missing = []
        for tu in m.tool_uses or []:
            if tu.tool_use_id in resolved:
                continue
            missing.append(
                ToolResultBlock(
                    tool_use_id=tu.tool_use_id,
                    content=INTERRUPTED_TOOL_RESULT,
                    is_error=True,
                )
            )
            resolved.add(tu.tool_use_id)
        if missing:
            out.append(Message(role="user", content="", tool_results=missing))
    return out


# 接在重放结果前面，说明这段是「恢复会话时从执行轨迹账本补回来的真实结果」，
# 让模型知道工具当时确实已有结局、不必再重做。
REPLAYED_TOOL_RESULT_PREFIX = (
    "[Replayed from execution ledger] This tool call was already resolved before an "
    "earlier interruption. Its recorded result is reproduced below verbatim:\n"
)


def resolve_dangling_tool_uses(
    messages: list[Message],
    resolve: Callable[[str], tuple[str, bool] | None],
) -> list[Message]:
    """用执行轨迹账本补全中断留下的悬空 tool_use，返回副本、不改输入。

    `resolve` 接收 tool_use_id，返回 (content, is_error)；返回 None 表示账本里
    没有该调用的终态记录（可能从没执行，或执行到一半被打断、结局未知），这条
    悬空 tool_use 原样保留，交由请求前的 ensure_tool_pairing 用「interrupted」
    措辞兜底。

    补出来的结果直接写回历史：它如实记录了「发生过什么」，resume 时模型看到真实
    结果就不会再把已经生效的动作重做一遍。这里只补有据可查的终态，不碰结局未知的。
    """
    resolved = {tr.tool_use_id for m in messages for tr in (m.tool_results or [])}
    out: list[Message] = []
    for m in messages:
        out.append(m)
        pending: list[ToolResultBlock] = []
        for tu in m.tool_uses or []:
            if tu.tool_use_id in resolved:
                continue
            recorded = resolve(tu.tool_use_id)
            if recorded is None:
                continue
            content, is_error = recorded
            pending.append(
                ToolResultBlock(
                    tool_use_id=tu.tool_use_id,
                    content=REPLAYED_TOOL_RESULT_PREFIX + content,
                    is_error=is_error,
                )
            )
            resolved.add(tu.tool_use_id)
        if pending:
            out.append(Message(role="user", content="", tool_results=pending))
    return out
