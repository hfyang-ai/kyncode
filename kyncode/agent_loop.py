from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kyncode.context import (
    CompactBoundary,
    CompactEvent,
    apply_tool_result_budget,
    auto_compact,
    is_spill_readback,
)
from kyncode.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from kyncode.conversation import ThinkingBlock as ConvThinkingBlock
from kyncode.permissions.approval import PermissionRequest
from kyncode.prompts import (
    build_environment_context,
    build_plan_mode_reminder,
    build_system_prompt,
)
from kyncode.tools import ToolRegistry
from kyncode.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)

if TYPE_CHECKING:
    from kyncode.agent import Agent

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 循环常量
# ---------------------------------------------------------------------------

# 每隔多少轮做一次记忆提取
MEMORY_EXTRACTION_INTERVAL = 5
# 传给记忆召回选择器的最近工具名上限，去重后保留最近这么多个
MAX_RECENT_TOOLS = 10
MAX_TOKENS_CEILING = 64000
MAX_OUTPUT_TOKENS_RECOVERIES = 3

# 延迟工具清单提醒的固定开头。用它在历史里回认这条提醒还在不在：compact 把历史压
# 成摘要之后原来那条就没了，得重发一遍
DEFERRED_REMINDER_MARKER = "The following deferred tools are available via ToolSearch."


# ---------------------------------------------------------------------------
# AgentEvent 事件类型
# ---------------------------------------------------------------------------


@dataclass
class StreamText:
    text: str


@dataclass
class ThinkingText:
    text: str


@dataclass
class RetryEvent:
    reason: str
    wait: float = 0.0


@dataclass
class ToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict[str, Any]


@dataclass
class ToolResultEvent:
    tool_id: str
    tool_name: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class TurnComplete:
    turn: int


@dataclass
class LoopComplete:
    total_turns: int


@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass
class ErrorEvent:
    message: str


@dataclass
class CompactNotification:
    before_tokens: int
    message: str
    # 压缩重建后历史的估算 token 数（与 before_tokens 一起展示「回收了多少」）。
    after_tokens: int = 0
    # 结构化 boundary（摘要 + 原文保留尾部），UI/session 层用它持久化 compact_boundary 记录。
    # 失败路径下为 None。
    boundary: CompactBoundary | None = None


@dataclass
class HookEvent:
    hook_id: str
    event: str
    output: str
    success: bool


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | LoopComplete
    | UsageEvent
    | ErrorEvent
    | PermissionRequest
    | CompactNotification
    | HookEvent
)


# ---------------------------------------------------------------------------
# LLM 响应收集器
# ---------------------------------------------------------------------------


@dataclass
class ThinkingBlock:
    thinking: str
    signature: str


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    stop_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0


class StreamCollector:
    def __init__(self) -> None:
        self.response = LLMResponse()

    async def consume(
        self, stream: AsyncIterator[StreamEvent]
    ) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self.response.text += event.text
                yield StreamText(text=event.text)
            elif isinstance(event, ThinkingDelta):
                yield ThinkingText(text=event.text)
            elif isinstance(event, ThinkingComplete):
                self.response.thinking_blocks.append(
                    ThinkingBlock(thinking=event.thinking, signature=event.signature)
                )
            elif isinstance(event, (ToolCallStart, ToolCallDelta)):
                pass
            elif isinstance(event, ToolCallComplete):
                self.response.tool_calls.append(event)
                yield ToolUseEvent(
                    tool_name=event.tool_name,
                    tool_id=event.tool_id,
                    arguments=event.arguments,
                )
            elif isinstance(event, StreamEnd):
                self.response.stop_reason = event.stop_reason
                self.response.input_tokens = event.input_tokens
                self.response.output_tokens = event.output_tokens
                self.response.cache_read = event.cache_read
                self.response.cache_creation = event.cache_creation


# ---------------------------------------------------------------------------
# tool 批量执行
# ---------------------------------------------------------------------------


@dataclass
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


def partition_tool_calls(
    tool_calls: list[ToolCallComplete],
    registry: ToolRegistry,
) -> list[ToolBatch]:
    """把工具调用按相邻性分批：连续的并发安全的调用归到同一个批次，其余各自独占一个
    串行批次。并发安全的调用不改外部状态，同时跑没有互相干扰的余地；会改东西的一旦
    并发，执行顺序就不再是模型给出的那个顺序。

    安不安全按这一次调用的实际参数算，不是只看工具类别。ls 和 rm 都是 Bash，前者
    可以跟 ReadFile 一起并发，后者必须独占。
    """
    batches: list[ToolBatch] = []
    for tc in tool_calls:
        tool = registry.get(tc.tool_name)
        safe = tool is not None and tool.is_concurrency_safe(tc.arguments)

        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(tc)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[tc]))
    return batches


# ---------------------------------------------------------------------------
# streaming 执行器 — 在 LLM streaming 期间启动 tool 执行
# ---------------------------------------------------------------------------


@dataclass
class ToolExecResult:
    tool_id: str
    tool_name: str
    result: ToolResult
    elapsed: float


class StreamingExecutor:
    """收集流式期间到达的工具调用，等流结束后按批次执行。

    收集而不是收到就跑：只读工具可以并发，写和命令类工具必须串行，而怎么分批
    要看调用之间的相邻关系，只有收齐了才分得出来。收到就跑等于全部并发，模型在
    一轮里发两个 EditFile 改同一个文件、或者 WriteFile 之后紧跟一条依赖它的
    Bash，执行顺序就不再是模型给出的那个顺序。
    """

    def __init__(self) -> None:
        self._calls: list[ToolCallComplete] = []

    def submit(self, tc: ToolCallComplete) -> None:
        self._calls.append(tc)

    def has_pending(self) -> bool:
        return bool(self._calls)

    def reset(self) -> None:
        self._calls = []

    @staticmethod
    def _error_result(tc: ToolCallComplete, exc: BaseException) -> ToolExecResult:
        """把异常收成一条错误结果，tool_id 必须带上。

        缺了 tool_id 这条结果就配不上对应的 tool_use，下一轮请求会被 API 直接拒掉。
        """
        return ToolExecResult(
            tool_id=tc.tool_id,
            tool_name=tc.tool_name,
            result=ToolResult(output=f"Tool execution error: {exc}", is_error=True),
            elapsed=0.0,
        )

    async def execute_all(
        self,
        registry: ToolRegistry,
        run_one: Callable[[ToolCallComplete], Any],
    ) -> list[ToolExecResult]:
        """按批次执行收集到的调用，只读批并发，写和命令批串行。

        批次本身有序、批内也有序，所以按批拼接出来的结果顺序就是模型给出的顺序。
        """
        if not self._calls:
            return []

        out: list[ToolExecResult] = []
        for batch in partition_tool_calls(self._calls, registry):
            if batch.concurrent and len(batch.calls) > 1:
                gathered = await asyncio.gather(
                    *(run_one(tc) for tc in batch.calls), return_exceptions=True
                )
                for tc, r in zip(batch.calls, gathered):
                    out.append(
                        self._error_result(tc, r) if isinstance(r, BaseException) else r
                    )
            else:
                for tc in batch.calls:
                    try:
                        out.append(await run_one(tc))
                    except Exception as exc:  # noqa: BLE001 - 单个工具失败不该中断整批
                        out.append(self._error_result(tc, exc))
        return out


async def run_agent_loop(
    agent: Agent,
    conversation: ConversationManager,
) -> AsyncIterator[AgentEvent]:
    agent._current_conversation = conversation
    env_context = build_environment_context(
        agent.work_dir, agent.active_skills, agent._skill_catalog, agent._agent_catalog
    )
    conversation.inject_environment(env_context)

    memory_content = agent.memory_manager.load() if agent.memory_manager else ""
    conversation.inject_long_term_memory(agent.instructions_content, memory_content)

    if agent.hook_engine:
        ctx = agent.build_hook_context("session_start")
        await agent.hook_engine.run_hooks("session_start", ctx)
        for he in agent.drain_hook_events():
            yield he

    iteration = 0
    max_tokens_escalated = False
    output_recoveries = 0

    while True:
        iteration += 1

        if agent.max_iterations > 0 and iteration > agent.max_iterations:
            yield ErrorEvent(
                message=f"Agent reached maximum iterations ({agent.max_iterations})"
            )
            break

        if agent.hook_engine:
            ctx = agent.build_hook_context("turn_start")
            await agent.hook_engine.run_hooks("turn_start", ctx)
            for he in agent.drain_hook_events():
                yield he

        agent.consume_mailbox(conversation)
        if agent.notification_fn:
            for note in agent.notification_fn():
                conversation.add_system_reminder(note)

        if agent.hook_engine:
            ctx = agent.build_hook_context("pre_send")
            await agent.hook_engine.run_hooks("pre_send", ctx)
            for he in agent.drain_hook_events():
                yield he

        hook_prompts = (
            agent.hook_engine.get_prompt_messages() if agent.hook_engine else None
        )
        system = build_system_prompt(hook_prompts=hook_prompts, work_dir=agent.work_dir)

        if agent.plan_mode:
            plan_path = str(agent.get_plan_path())
            if agent.permission_checker:
                agent.permission_checker.plan_file_path = plan_path
            plan_exists = agent.get_plan_path().exists()
            plan_reminder = build_plan_mode_reminder(plan_path, plan_exists, iteration)
            conversation.add_system_reminder(plan_reminder)

        # Coordinator 模式：工具集被收窄的同时注入调度指引。
        # 走 system-reminder 而不是替换系统提示词：长会话里开头那份约束会被淹没，
        # 每轮追加一次才拉得回来，而且 Lead 仍然需要身份、环境、项目指令和记忆这些基础段落。
        if agent.coordinator_mode:
            from kyncode.teams.coordinator import get_coordinator_reminder

            conversation.add_system_reminder(
                get_coordinator_reminder(
                    iteration,
                    agent_catalog=agent._agent_catalog_list or None,
                )
            )

        if agent.hook_engine:
            for note in agent.hook_engine.drain_notifications():
                conversation.add_system_reminder(
                    f"Hook [{note.hook_id}] {note.event}: {note.output}"
                )

        agent.announce_deferred_tools(conversation)

        tools = agent.registry.get_all_schemas(agent.protocol)

        # Layer 2: 接近 context window 上限时自动 compact
        # Layer 1（工具结果预算）在结果入历史时已处理完，历史里的内容
        # 就是最终大小，直接用 conversation.history 估算
        compact_result = await auto_compact(
            conversation,
            agent.client,
            agent.context_window,
            agent.session_dir,
            protocol=agent.protocol,
            breaker=agent.compact_breaker,
            recovery=agent.recovery_state,
            tool_schemas=agent.registry.get_all_schemas(agent.protocol),
            transcript_path=agent._transcript_path,
        )
        if isinstance(compact_result, CompactEvent):
            yield CompactNotification(
                before_tokens=compact_result.before_tokens,
                after_tokens=compact_result.after_tokens,
                message=(
                    f"上下文已压缩（{compact_result.before_tokens:,} → "
                    f"{compact_result.after_tokens:,} tokens，回收 "
                    f"{compact_result.before_tokens - compact_result.after_tokens:,}）"
                ),
                boundary=compact_result.boundary,
            )
            conversation.inject_environment(env_context)
            mem = agent.memory_manager.load() if agent.memory_manager else ""
            conversation.inject_long_term_memory(agent.instructions_content, mem)
        elif isinstance(compact_result, str):
            yield ErrorEvent(message=compact_result)

        collector = StreamCollector()
        executor = StreamingExecutor()
        deferred_tool_calls: list[ToolCallComplete] = []
        llm_stream = agent.client.stream(conversation, system=system, tools=tools)
        async for event in collector.consume(llm_stream):
            # 流式工具执行：收到完整 tool_use 就立刻提交执行，不等整个响应结束
            if isinstance(event, ToolUseEvent):
                tc = collector.response.tool_calls[-1]
                # 需要交互式权限确认的工具延迟到流结束后顺序执行
                tool = agent.registry.get(tc.tool_name)
                needs_ask = False
                if tool and agent.permission_checker:
                    decision = agent.permission_checker.check(tool, tc.arguments)
                    needs_ask = decision.effect == "ask"
                if needs_ask:
                    deferred_tool_calls.append(tc)
                else:
                    executor.submit(tc)
            yield event

        response = collector.response
        agent.log_llm_response(response, iteration)

        if agent.hook_engine:
            ctx = agent.build_hook_context("post_receive", message=response.text)
            await agent.hook_engine.run_hooks("post_receive", ctx)
            for he in agent.drain_hook_events():
                yield he

        agent.total_input_tokens += response.input_tokens
        agent.total_output_tokens += response.output_tokens
        yield UsageEvent(
            input_tokens=agent.total_input_tokens,
            output_tokens=agent.total_output_tokens,
        )

        conv_thinking = [
            ConvThinkingBlock(thinking=tb.thinking, signature=tb.signature)
            for tb in response.thinking_blocks
        ]

        if response.stop_reason == "max_tokens":
            if not max_tokens_escalated:
                agent.client.set_max_output_tokens(MAX_TOKENS_CEILING)
                max_tokens_escalated = True
                if response.text:
                    conversation.add_assistant_message(
                        response.text, thinking_blocks=conv_thinking
                    )
                    conversation.add_user_message(
                        "Output token limit hit. Resume directly from where you stopped. "
                        "Do not apologize or repeat previous content. Pick up mid-thought if needed."
                    )
                yield RetryEvent(reason="max_tokens escalation")
                continue
            elif output_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES:
                output_recoveries += 1
                conversation.add_assistant_message(
                    response.text, thinking_blocks=conv_thinking
                )
                conversation.add_user_message(
                    "Output token limit hit. Resume directly from where you stopped. "
                    "Break remaining work into smaller pieces."
                )
                yield RetryEvent(
                    reason=f"max_tokens recovery {output_recoveries}/{MAX_OUTPUT_TOKENS_RECOVERIES}"
                )
                continue
        else:
            output_recoveries = 0

        if not response.tool_calls:
            conversation.add_assistant_message(
                response.text, thinking_blocks=conv_thinking
            )
            agent.set_loop_count(agent.get_loop_count() + 1)
            if (
                agent.get_loop_count() % MEMORY_EXTRACTION_INTERVAL == 0
                and agent.memory_manager
            ):
                asyncio.ensure_future(agent.extract_memories(conversation))
            if agent._consolidator is not None:
                asyncio.ensure_future(
                    agent._consolidator.maybe_run(
                        agent.client, conversation, agent.protocol
                    )
                )
            if agent.hook_engine:
                ctx = agent.build_hook_context("turn_end")
                await agent.hook_engine.run_hooks("turn_end", ctx)
                ctx = agent.build_hook_context("session_end")
                await agent.hook_engine.run_hooks("session_end", ctx)
                for he in agent.drain_hook_events():
                    yield he
            if agent.file_history is not None:
                summary = (
                    response.text[:60] + "..."
                    if len(response.text) > 60
                    else response.text
                )
                agent.file_history.make_snapshot(len(conversation.history), summary)
            yield LoopComplete(total_turns=iteration)
            break

        tool_uses = [
            ToolUseBlock(
                tool_use_id=tc.tool_id,
                tool_name=tc.tool_name,
                arguments=tc.arguments,
            )
            for tc in response.tool_calls
        ]
        conversation.add_assistant_message(
            response.text, tool_uses, thinking_blocks=conv_thinking
        )
        # 在 assistant 回复加入历史后锚定实际用量：基线（input + cache + output）
        # 覆盖到当前位置，因此下一轮迭代顶部的 auto-compact 检查只需对
        # 接下来追加的 tool results 做字符估算。
        conversation.record_usage_anchor(
            response.input_tokens,
            response.output_tokens,
            response.cache_read,
            response.cache_creation,
        )

        # 溢写文件的回读结果豁免溢写：把模型刚读回来的内容再写盘换成
        # 预览，模型就永远看不到全文，还会在「读回、溢写」之间打转
        exempt_ids = {
            tc.tool_id
            for tc in response.tool_calls
            if is_spill_readback(tc.tool_name, tc.arguments, agent.session_dir)
        }

        # 收集流式执行器中已提交的工具结果（工具在 LLM 流式输出期间已开始执行）
        tool_results: list[ToolResultBlock] = []
        streaming_results = await executor.execute_all(
            agent.registry, agent._execute_single_tool_direct
        )

        for br in streaming_results:
            content = agent.maybe_persist_or_truncate(
                br.tool_id, br.result.output, exempt_ids
            )
            tool_results.append(
                ToolResultBlock(
                    tool_use_id=br.tool_id,
                    content=content,
                    is_error=br.result.is_error,
                    content_blocks=br.result.content_blocks,
                )
            )
            yield ToolResultEvent(
                tool_id=br.tool_id,
                tool_name=br.tool_name,
                output=br.result.output,
                is_error=br.result.is_error,
                elapsed=br.elapsed,
            )

        # 需要交互式权限确认的工具，在流结束后顺序执行
        for tc in deferred_tool_calls:
            result: ToolResult | None = None
            elapsed = 0.0

            async for item in agent.execute_tool(tc):
                if isinstance(item, PermissionRequest):
                    yield item
                else:
                    result, elapsed = item

            if result is None:
                result = ToolResult(output="Error: no result from tool", is_error=True)

            content = agent.maybe_persist_or_truncate(
                tc.tool_id, result.output, exempt_ids
            )
            tool_results.append(
                ToolResultBlock(
                    tool_use_id=tc.tool_id,
                    content=content,
                    is_error=result.is_error,
                    content_blocks=result.content_blocks,
                )
            )
            yield ToolResultEvent(
                tool_id=tc.tool_id,
                tool_name=tc.tool_name,
                output=result.output,
                is_error=result.is_error,
                elapsed=elapsed,
            )

        exit_plan_called = any(
            tc.tool_name == "ExitPlanMode" for tc in response.tool_calls
        )
        # 聚合预算：一轮并行工具的结果落在同一条消息里，单条阈值管不住
        # 合计超限的情况。进历史前把整批处理完，消息一出生就是终态
        apply_tool_result_budget(tool_results, agent.session_dir, exempt_ids)
        conversation.add_tool_results_message(tool_results)

        # 工具结果已入历史，本轮 ReadFile / skill 调用 / 编辑产生的运行时
        # 心智（已读文件、激活技能、技能快照）已更新，落盘供崩溃恢复。
        agent.save_runtime_state()

        # 非阻塞 memory recall：工具执行完后检查 prefetch 是否就绪
        if (
            agent.memory_recall_task
            and not agent._memory_recall_consumed
            and agent.memory_recall_task.done()
        ):
            try:
                recall = agent.memory_recall_task.result()
                if recall and recall.reminder:
                    conversation.add_system_reminder(recall.reminder)
                    # 真正进了对话才算「已注入」。这一轮没消费掉的召回结果
                    # 不留痕，下一轮召回时这些记忆还能参选。
                    agent.surfaced_memory_paths.update(recall.paths)
            except Exception:
                pass
            agent._memory_recall_consumed = True

        if exit_plan_called:
            yield TurnComplete(turn=iteration)
            yield LoopComplete(total_turns=iteration)
            break

        if agent.hook_engine:
            ctx = agent.build_hook_context("turn_end")
            await agent.hook_engine.run_hooks("turn_end", ctx)
            for he in agent.drain_hook_events():
                yield he
        yield TurnComplete(turn=iteration)


async def run_agent_loop_to_completion(
    agent: Agent,
    task: str,
    conversation: ConversationManager | None = None,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    if conversation is None:
        conversation = ConversationManager()

        env_context = build_environment_context(
            agent.work_dir,
            agent.active_skills,
            agent._skill_catalog,
            agent._agent_catalog,
        )
        conversation.inject_environment(env_context)

        if agent.instructions_content:
            memory_content = agent.memory_manager.load() if agent.memory_manager else ""
            conversation.inject_long_term_memory(
                agent.instructions_content, memory_content
            )

    if task:
        conversation.add_user_message(task)

    hook_prompts = (
        agent.hook_engine.get_prompt_messages() if agent.hook_engine else None
    )
    system = build_system_prompt(hook_prompts=hook_prompts, work_dir=agent.work_dir)

    tools = agent.registry.get_all_schemas(agent.protocol)

    log.info(
        "[run_to_completion] agent=%s tools=%d names=%s coordinator=%s",
        agent.agent_id,
        len(tools),
        [t["name"] for t in tools][:10],
        agent.coordinator_mode,
    )

    last_text = ""

    iteration = 0
    while True:
        iteration += 1
        if agent.max_iterations > 0 and iteration > agent.max_iterations:
            break
        if agent.hook_engine:
            ctx = agent.build_hook_context("turn_start")
            await agent.hook_engine.run_hooks("turn_start", ctx)

        agent.consume_mailbox(conversation)
        if agent.notification_fn:
            for note in agent.notification_fn():
                conversation.add_system_reminder(note)

        compact_result = await auto_compact(
            conversation,
            agent.client,
            agent.context_window,
            agent.session_dir,
            protocol=agent.protocol,
            breaker=agent.compact_breaker,
            recovery=agent.recovery_state,
            tool_schemas=agent.registry.get_all_schemas(agent.protocol),
            transcript_path=agent._transcript_path,
        )
        if isinstance(compact_result, CompactEvent):
            conversation.inject_environment(env_context)

        agent.announce_deferred_tools(conversation)

        collector = StreamCollector()
        llm_stream = agent.client.stream(conversation, system=system, tools=tools)
        async for _event in collector.consume(llm_stream):
            pass

        response = collector.response
        agent.log_llm_response(response, iteration)
        agent.total_input_tokens += response.input_tokens
        agent.total_output_tokens += response.output_tokens

        if event_callback:
            event_callback(
                {
                    "type": "usage",
                    "usage": {
                        "inputTokens": agent.total_input_tokens,
                        "outputTokens": agent.total_output_tokens,
                    },
                }
            )

        if response.text:
            last_text = response.text
            if event_callback:
                event_callback(
                    {
                        "type": "stream_text",
                        "text": response.text,
                    }
                )

        log.info(
            "[run_to_completion] agent=%s iter=%d tool_calls=%d text_len=%d stop=%s",
            agent.agent_id,
            iteration,
            len(response.tool_calls),
            len(response.text),
            response.stop_reason,
        )

        if not response.tool_calls:
            conversation.add_assistant_message(response.text)
            if agent.file_history is not None:
                summary = (
                    response.text[:60] + "..."
                    if len(response.text) > 60
                    else response.text
                )
                agent.file_history.make_snapshot(len(conversation.history), summary)
            break

        tool_uses = [
            ToolUseBlock(
                tool_use_id=tc.tool_id,
                tool_name=tc.tool_name,
                arguments=tc.arguments,
            )
            for tc in response.tool_calls
        ]
        conversation.add_assistant_message(response.text, tool_uses)
        # assistant 回复已在历史中，锚定实际用量；下一轮迭代只需对
        # 下方追加的 tool results 做字符估算。
        conversation.record_usage_anchor(
            response.input_tokens,
            response.output_tokens,
            response.cache_read,
            response.cache_creation,
        )

        # 溢写文件的回读结果豁免溢写：把模型刚读回来的内容再写盘换成
        # 预览，模型就永远看不到全文，还会在「读回、溢写」之间打转
        exempt_ids = {
            tc.tool_id
            for tc in response.tool_calls
            if is_spill_readback(tc.tool_name, tc.arguments, agent.session_dir)
        }

        tool_results: list[ToolResultBlock] = []
        for tc in response.tool_calls:
            if event_callback:
                event_callback(
                    {
                        "type": "tool_use",
                        "toolName": tc.tool_name,
                        "args": tc.arguments,
                    }
                )
            result = await agent.execute_tool_noninteractive(tc)
            content = agent.maybe_persist_or_truncate(
                tc.tool_id, result.output, exempt_ids
            )
            tool_results.append(
                ToolResultBlock(
                    tool_use_id=tc.tool_id,
                    content=content,
                    is_error=result.is_error,
                    content_blocks=result.content_blocks,
                )
            )

        # 聚合预算：一轮并行工具的结果落在同一条消息里，单条阈值管不住
        # 合计超限的情况。进历史前把整批处理完，消息一出生就是终态
        apply_tool_result_budget(tool_results, agent.session_dir, exempt_ids)
        conversation.add_tool_results_message(tool_results)
        agent.save_runtime_state()

        if agent.hook_engine:
            ctx = agent.build_hook_context("turn_end")
            await agent.hook_engine.run_hooks("turn_end", ctx)

    return last_text
