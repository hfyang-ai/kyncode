from __future__ import annotations

from kyncode.commands.registry import Command, CommandContext, CommandType
from kyncode.conversation import ConversationManager


def _resolve_interrupted_tool_uses(ctx, session_id: str, messages):
    """恢复会话时，用执行轨迹账本补全中断留下的悬空 tool_use。

    优先复用 agent 工具执行管线里已挂载的 ledger（resume 时已经 load 过），
    把有终态记录的悬空 tool_use 用真实结果回填。没有 ledger 或没有 agent
    时原样返回，不改变既有 resume 行为。
    """
    if ctx.agent is None:
        return messages
    ledger = getattr(ctx.agent.tool_runtime, "ledger", None)
    if ledger is None:
        return messages

    from kyncode.conversation_pairing import resolve_dangling_tool_uses

    def resolve(tool_use_id: str):
        entry = ledger.committed_by_id.get(tool_use_id)
        if entry is None or entry.result is None:
            return None
        return (
            str(entry.result.get("output", "")),
            bool(entry.result.get("is_error", False)),
        )

    return resolve_dangling_tool_uses(messages, resolve)


async def handle_session(ctx: CommandContext) -> None:
    sm = ctx.session_manager
    if sm is None:
        ctx.ui.add_system_message("会话管理器未初始化")
        return

    parts = ctx.args.split(None, 1)
    sub = parts[0] if parts else ""

    if sub == "":
        if ctx.session:
            m = ctx.session.meta
            ts = m.last_active.strftime("%Y-%m-%d %H:%M")
            ctx.ui.add_system_message(
                f"当前会话: {m.id}\n"
                f"  标题: {m.title or '(未命名)'}\n"
                f"  消息: {m.message_count} 条\n"
                f"  Token: {m.total_tokens:,}\n"
                f"  最后活跃: {ts}"
            )
        else:
            ctx.ui.add_system_message("当前没有活跃会话")
        return

    if sub == "list":
        metas = sm.list()
        if not metas:
            ctx.ui.add_system_message("没有已保存的会话。")
            return
        lines: list[str] = ["会话列表："]
        for m in metas[:10]:
            ts = m.last_active.strftime("%Y-%m-%d %H:%M")
            title = m.title or "(未命名)"
            lines.append(f"  {m.id}  {title}  [{m.message_count} msgs, {ts}]")
        ctx.ui.add_system_message("\n".join(lines))

    elif sub == "resume":
        session_id = parts[1].strip() if len(parts) > 1 else ""
        if not session_id:
            metas = sm.list()
            if not metas:
                ctx.ui.add_system_message("没有已保存的会话。")
                return
            lines: list[str] = [
                "可恢复的会话（使用 /session resume <id> 或 /session resume <序号>）："
            ]
            for i, m in enumerate(metas[:15], 1):
                ts = m.last_active.strftime("%Y-%m-%d %H:%M")
                title = m.title or "(未命名)"
                lines.append(
                    f"  {i}. [{m.id[:8]}]  {title}  ({m.message_count} msgs, {ts})"
                )
            ctx.ui.add_system_message("\n".join(lines))
            return
        # 序号解析：实时查会话列表（与上面无参展示用同一份前 15 条），不依赖跨
        # 命令的临时状态——config 每次命令分发都会重建，存进去下一条命令读不到。
        if session_id.isdigit():
            metas = sm.list()[:15]
            idx = int(session_id) - 1
            if 0 <= idx < len(metas):
                session_id = metas[idx].id
        result = sm.resume(session_id)
        if result is None:
            ctx.ui.add_system_message(f"会话未找到: {session_id}")
            return
        if ctx.session:
            ctx.session.close()
        ctx.config["set_session"](result.session)
        messages = result.messages
        # 恢复后先用执行轨迹账本补全中断留下的悬空 tool_use：能查到终态记录的
        # 直接用真实结果回填，模型看到结果就不会把已经生效的动作重做一遍。
        messages = _resolve_interrupted_tool_uses(ctx, session_id, result.messages)
        conv = ConversationManager()
        for msg in messages:
            conv.history.append(msg)
        ctx.config["set_conversation"](conv)
        if ctx.agent:
            ctx.agent._loop_count = 0
            # 恢复运行时心智：激活的 skill、已读文件、技能快照。set_session 已把
            # agent.session_id 切到当前会话，state 路径随之就位。
            ctx.agent.load_runtime_state()
            notice = ctx.agent.build_resume_notice()
            if notice:
                # 注入给模型的一条恢复提示（不参与 UI 渲染）：哪些文件上次读过
                # 且未变、哪些变了需重读、哪些 skill 仍激活。
                conv.add_system_reminder(notice)
        await ctx.config["render_restored"](messages)
        ctx.ui.add_system_message(
            f"会话已恢复: {session_id} ({result.session.meta.message_count} msgs)"
        )

    elif sub == "new":
        if ctx.session:
            ctx.session.close()
        new_session = sm.create()
        ctx.config["set_session"](new_session)
        ctx.config["set_conversation"](ConversationManager())
        if ctx.agent:
            ctx.agent._loop_count = 0
        ctx.config["clear_chat"]()
        ctx.ui.add_system_message(f"新会话已创建: {new_session.session_id}")

    elif sub == "delete":
        session_id = parts[1].strip() if len(parts) > 1 else ""
        if not session_id:
            ctx.ui.add_system_message("用法: /session delete <id>")
            return
        if ctx.session and ctx.session.session_id == session_id:
            ctx.ui.add_system_message("不能删除当前活跃的会话。")
            return
        if sm.delete(session_id):
            ctx.ui.add_system_message(f"会话已删除: {session_id}")
        else:
            ctx.ui.add_system_message(f"会话未找到: {session_id}")

    else:
        ctx.ui.add_system_message(
            "用法: /session [list | resume <id> | new | delete <id>]"
        )


SESSION_COMMAND = Command(
    name="session",
    description="会话管理",
    usage="/session [list | resume <id> | new | delete <id>]",
    type=CommandType.LOCAL,
    handler=handle_session,
)
