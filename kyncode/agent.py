from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from kyncode.agent_loop import (
    DEFERRED_REMINDER_MARKER,
    MAX_RECENT_TOOLS,
    AgentEvent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LLMResponse,
    ToolExecResult,
    run_agent_loop,
    run_agent_loop_to_completion,
)
from kyncode.client import LLMClient
from kyncode.context import (
    READBACK_TOKEN_LIMIT,
    CompactCircuitBreaker,
    CompactEvent,
    RecoveryState,
    auto_compact,
    ensure_session_dir,
)
from kyncode.conversation import (
    ConversationManager,
    estimate_text_tokens,
)
from kyncode.hooks import HookContext, HookEngine
from kyncode.memory.auto_memory import MemoryManager
from kyncode.permissions import (
    PermissionChecker,
    PermissionMode,
)
from kyncode.permissions.approval import (
    PermissionReply,  # noqa: F401 — 向后兼容 re-export，供 app/remote/tests 从 kyncode.agent 导入
    PermissionRequest,
    PermissionResponse,  # noqa: F401 — 向后兼容 re-export
)
from kyncode.prompts import build_environment_context
from kyncode.tools import ToolRegistry
from kyncode.tools.base import (
    MAX_OUTPUT_TOKENS,
    ToolCallComplete,
    ToolResult,
)
from kyncode.tools.redact import redact_sensitive
from kyncode.tools.runtime import ToolInvocation, ToolRuntime
from kyncode.tracing import (
    TRACE_ARGS_LIMIT,
    TRACE_TEXT_LIMIT,
    TRACE_THINKING_LIMIT,
    current_agent_id,
    truncate,
)

log = logging.getLogger(__name__)

# 事件类型 / LLM 收集器 / 工具批量执行器 / 循环常量，以及 run 的主循环编排，
# 都定义在 kyncode.agent_loop。这里只导入 Agent 内部真正用到的几个符号，
# 事件类与 loop 函数由调用方（app/remote/tests）直接从 agent_loop 导入。


class Agent:
    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry,
        protocol: str,
        work_dir: str = ".",
        max_iterations: int = 0,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: MemoryManager | None = None,
        hook_engine: HookEngine | None = None,
    ) -> None:
        self.client = client
        self.registry = registry
        self.protocol = protocol
        self.work_dir = work_dir
        self.max_iterations = max_iterations
        self.permission_checker = permission_checker
        self.permission_mode: PermissionMode = (
            permission_checker.mode if permission_checker else PermissionMode.DEFAULT
        )
        # 工具执行管线（prepare → execute → finalize）。三个执行路径都委托给它，
        # 交互式审批路径额外传入 interactive=True。
        self.tool_runtime = ToolRuntime(registry, permission_checker=permission_checker)
        self.context_window = context_window
        self.compact_breaker = CompactCircuitBreaker()
        # 保存重建工作上下文所需的快照，在 Layer 2 压缩对话后使用：
        # 最近的文件读取和 skill 调用。每次 ReadFile / skill 调用时记录，
        # auto_compact 触发阈值时消费。
        self.recovery_state: RecoveryState = RecoveryState()
        # 最近调用过的工具名，去重后按调用顺序保留。传给记忆召回的选择器，
        # 让它跳过这些工具的用法说明类记忆（正在用的东西不需要说明书），
        # 但涉及坑和警告的记忆仍然要选出来。
        self.recent_tool_names: list[str] = []
        # 本次会话已经注入过的记忆文件路径。召回前做预过滤，避免同一条记忆
        # 每轮重新占用选择器的名额，也避免同样的正文反复进上下文。
        self.surfaced_memory_paths: set[str] = set()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        # 本 agent 真正执行（executed=True）过的工具调用次数。三个执行路径
        # 各自累加，完成时由 AgentTool / TaskManager 写进追踪节点。
        self.tool_call_count = 0
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        self.hook_engine = hook_engine
        self._loop_count = 0
        # 上一次告诉模型的延迟工具清单，按字典序。跟当前清单一比就知道工具池有没有
        # 变，没变就不重发那条提醒
        self._announced_deferred: list[str] = []
        # 记忆提取合并策略：_extracting 期间触发新请求会标记 _pending_extraction，
        # _extracting: 标记是否有提取正在进行
        # _pending_extraction: 提取期间又触发了新请求，标记需要尾随提取
        self._extracting = False
        self._pending_extraction = False
        self._consolidator: MemoryConsolidator | None = None
        if memory_manager is not None:
            from kyncode.memory.consolidation import MemoryConsolidator

            self._consolidator = MemoryConsolidator(work_dir, sub_agent_factory=Agent)
        self.session_id: str = ""
        self.active_skills: dict[str, str] = {}
        self._skill_catalog: str = ""
        self._agent_catalog: str = ""
        self._agent_catalog_list: list[tuple[str, str]] = []
        self.agent_id: str = uuid.uuid4().hex[:12]
        self.parent_id: str | None = None
        self.trace_id: str | None = None
        self.team_name: str = ""
        self._team_manager: Any = None
        # coordinator 模式的开关，由配置显式打开
        self.enable_coordinator_mode: bool = False
        self.notification_fn: Callable[[], list[str]] | None = None
        self.file_history: Any = None

        # 非阻塞 memory recall：prefetch task 与主 LLM 调用并行，工具执行后注入
        self.memory_recall_task: Any | None = None
        self._memory_recall_consumed: bool = False

    def announce_deferred_tools(self, conversation: ConversationManager) -> None:
        """把延迟工具名清单告诉模型，只在需要的时候发。

        dispatch 模式下这些工具永远不会进 tools[]，必须额外告诉模型调用要走
        mcp_call，否则它读完 schema 也不知道从哪儿调。

        这条提醒是 append 进历史的，发过一次就一直在上下文里，之后每轮再发一遍只
        是拿同样的内容占窗口：六十来个 MCP 工具一份清单五百多 token，四十轮下来
        就是两万多。所以只在两种情况重发，池子变了（MCP 是异步连上的，服务器也
        可能掉线重连），或者历史里那条已经被 compact 压掉了。后者靠回扫历史发现，
        这样就不用在 compact 那边额外挂钩子。
        """
        deferred_names = self.registry.get_deferred_tool_names()
        if not deferred_names:
            return

        pool_changed = deferred_names != self._announced_deferred
        if not pool_changed and conversation.has_reminder_containing(
            DEFERRED_REMINDER_MARKER
        ):
            return

        from kyncode.mcp.loading_strategy import McpLoadingMode

        tail = (
            ", then invoke them with the mcp_call tool"
            if self.registry.mcp_loading_mode is McpLoadingMode.DISPATCH
            else " before calling them"
        )
        conversation.add_system_reminder(
            DEFERRED_REMINDER_MARKER
            + " Their schemas are NOT loaded - use ToolSearch with "
            'query "select:<name>[,<name>...]" to load tool schemas'
            + tail
            + ":\n"
            + "\n".join(deferred_names)
        )
        self._announced_deferred = deferred_names

    @property
    def session_dir(self) -> Path:
        # 溢写目录跟随当前会话 id（resume 换会话后自动指向新目录）
        return ensure_session_dir(self.work_dir, self.session_id)

    @property
    def _transcript_path(self) -> str:
        if self.session_id:
            return str(
                Path(self.work_dir)
                / ".kyncode"
                / "sessions"
                / f"{self.session_id}.jsonl"
            )
        return ""

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN

    @property
    def coordinator_mode(self) -> bool:
        """coordinator 模式是否生效，只看配置开关。

        不看团队是否存在：模式在会话中途切换会留下麻烦，已经发出去的调度指引
        留在对话历史里撤不回来，模型会照着过期的约束继续做事。
        配置说了算，从第一轮到最后一轮都是同一套规则。
        """
        return self.enable_coordinator_mode

    _plan_path_cache: Path | None = None

    def get_plan_path(self) -> Path:
        if self._plan_path_cache is not None:
            return self._plan_path_cache
        import datetime
        import random

        _ADJECTIVES = [
            "bold",
            "bright",
            "calm",
            "cool",
            "deep",
            "fair",
            "fast",
            "fine",
            "glad",
            "keen",
            "kind",
            "lean",
            "mild",
            "neat",
            "pure",
            "safe",
            "slim",
            "soft",
            "tall",
            "warm",
            "wise",
            "grand",
            "swift",
            "vivid",
        ]
        _NOUNS = [
            "sketch",
            "draft",
            "spark",
            "bloom",
            "trail",
            "ridge",
            "creek",
            "grove",
            "cliff",
            "cloud",
            "field",
            "forge",
            "frost",
            "haven",
            "pearl",
            "stone",
            "storm",
            "river",
            "tower",
            "delta",
            "flame",
            "orbit",
            "pulse",
            "shore",
        ]
        plans_dir = Path(self.work_dir) / ".kyncode" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%m%d-%H%M")
        slug = f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-{ts}"
        self._plan_path_cache = plans_dir / f"{slug}.md"
        return self._plan_path_cache

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_mode = mode
        if self.permission_checker:
            self.permission_checker.mode = mode

    def get_loop_count(self) -> int:
        return self._loop_count

    def set_loop_count(self, value: int) -> None:
        self._loop_count = value

    def activate_skill(self, name: str, prompt_body: str) -> None:
        self.active_skills[name] = prompt_body

    def clear_active_skills(self) -> None:
        self.active_skills.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog

    def set_agent_catalog(
        self, catalog: str, catalog_list: list[tuple[str, str]] | None = None
    ) -> None:
        self._agent_catalog = catalog
        if catalog_list is not None:
            self._agent_catalog_list = catalog_list

    def build_hook_context(self, event: str, **kwargs: str | dict) -> HookContext:
        return HookContext(
            event_name=event,
            tool_name=str(kwargs.get("tool_name", "")),
            tool_args=kwargs.get("tool_args", {}),
            file_path=str(kwargs.get("file_path", "")),
            message=str(kwargs.get("message", "")),
            error=str(kwargs.get("error", "")),
        )

    def _infer_file_path(self, args: dict) -> str:
        return str(args.get("file_path", args.get("path", "")))

    def drain_hook_events(self) -> list[HookEvent]:
        if not self.hook_engine:
            return []
        return [
            HookEvent(
                hook_id=n.hook_id,
                event=n.event,
                output=n.output,
                success=n.success,
            )
            for n in self.hook_engine.drain_notifications()
        ]

    def log_llm_response(self, response: LLMResponse, iteration: int) -> None:
        """把一轮 LLM 响应（文本 / 思考 / 工具调用）写成结构化 trace 到 debug.log。

        文本、思考与工具参数都脱敏——模型可能回显邮箱、密钥，日志不应留存
        明文；超长内容截断，避免日志文件膨胀。整体序列化后统一脱敏一次。
        """
        thinking = "".join(tb.thinking for tb in response.thinking_blocks)
        payload = {
            "agent_id": self.agent_id,
            "iter": iteration,
            "text": truncate(response.text, TRACE_TEXT_LIMIT),
            "thinking": truncate(thinking, TRACE_THINKING_LIMIT),
            "tool_calls": [
                {
                    "tool_call_id": tc.tool_id,
                    "tool_name": tc.tool_name,
                    "arguments": truncate(
                        json.dumps(tc.arguments, ensure_ascii=False, default=str),
                        TRACE_ARGS_LIMIT,
                    ),
                }
                for tc in response.tool_calls
            ],
            "stop_reason": response.stop_reason,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
        log.info(
            "llm_response %s",
            redact_sensitive(json.dumps(payload, ensure_ascii=False, default=str)),
        )

    async def run(self, conversation: ConversationManager) -> AsyncIterator[AgentEvent]:
        token = current_agent_id.set(self.agent_id)
        try:
            async for event in run_agent_loop(self, conversation):
                yield event
        finally:
            current_agent_id.reset(token)

    def consume_mailbox(self, conversation: ConversationManager) -> None:
        if not self.team_name or not self._team_manager:
            return
        try:
            mailbox = self._team_manager.get_mailbox(self.team_name)
            if mailbox is None:
                return
            messages = mailbox.consume(self.agent_id)
            for msg in messages:
                prefix = f"[Message from {msg.from_agent}]"
                if msg.type != "text":
                    prefix = f"[{msg.type} from {msg.from_agent}]"
                content = f"{prefix} {msg.text}"
                conversation.add_user_message(content)
        except Exception as e:
            log.debug("Mailbox consumption failed: %s", e)

    async def _execute_single_tool_direct(self, tc: ToolCallComplete) -> ToolExecResult:
        invocation: ToolInvocation | None = None
        async for item in self.tool_runtime.invoke(tc, interactive=False):
            invocation = item
        assert invocation is not None
        if invocation.executed:
            self._record_recent_tool(tc.tool_name)
            self._snapshot_for_recovery(tc, invocation.result)
            self.tool_call_count += 1
        return ToolExecResult(
            tool_id=tc.tool_id,
            tool_name=tc.tool_name,
            result=invocation.result,
            elapsed=invocation.elapsed,
        )

    async def execute_tool(
        self, tc: ToolCallComplete
    ) -> AsyncIterator[PermissionRequest | tuple[ToolResult, float]]:
        invocation: ToolInvocation | None = None
        async for item in self.tool_runtime.invoke(tc, interactive=True):
            if isinstance(item, PermissionRequest):
                yield item
            else:
                invocation = item

        if invocation is None:
            result = ToolResult(output="Error: no result from tool", is_error=True)
            elapsed = 0.0
        else:
            result = invocation.result
            elapsed = invocation.elapsed
            if invocation.executed:
                self._record_recent_tool(tc.tool_name)
                self._snapshot_for_recovery(tc, result)
                self.tool_call_count += 1
        yield result, elapsed

    def _record_recent_tool(self, name: str) -> None:
        """记下刚执行完的工具名，供记忆召回的选择器参考。

        重复调用同一个工具时把它移到末尾，这样列表反映的是最近使用顺序
        而不是首次使用顺序。
        """
        if not name:
            return
        if name in self.recent_tool_names:
            self.recent_tool_names.remove(name)
        self.recent_tool_names.append(name)
        if len(self.recent_tool_names) > MAX_RECENT_TOOLS:
            del self.recent_tool_names[0]

    def _snapshot_for_recovery(self, tc: ToolCallComplete, result: ToolResult) -> None:
        """捕获 ReadFile 刚交给模型的内容，以便 Layer 2 压缩对话后
        auto_compact 能重新附加这些数据。每次 ReadFile 多一次磁盘读取，
        比从 tool 输出中反向解析行号要划算。
        """
        if result.is_error or tc.tool_name != "ReadFile":
            return
        path = tc.arguments.get("file_path") if isinstance(tc.arguments, dict) else None
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return
        self.recovery_state.record_file_read(path, content)

    async def extract_memories(self, conversation: ConversationManager) -> None:
        """触发记忆提取，合并策略见类内字段注释。

        当提取正在进行时，新的触发不会启动并发提取，而是标记 _pending_extraction。
        当前提取完成后检查该标志，如果有 pending 则立即执行一次尾随提取，
        防止多个触发器同时执行导致重复提取。
        """
        if not self.memory_manager:
            return

        # 合并策略：正在提取时暂存新请求，等当前提取完成后尾随执行
        if self._extracting:
            log.debug(
                "[extractMemories] extraction in progress — stashing for trailing run"
            )
            self._pending_extraction = True
            return

        self._extracting = True
        try:
            await self.memory_manager.extract(self.client, conversation, self.protocol)
        except Exception as e:
            log.debug("Memory extraction failed: %s", e)
        finally:
            self._extracting = False
            # 检查是否有尾随提取请求
            if self._pending_extraction:
                self._pending_extraction = False
                log.debug(
                    "[extractMemories] running trailing extraction for stashed context"
                )
                # 递归调用自身处理尾随请求
                await self.extract_memories(conversation)

    def maybe_persist_or_truncate(
        self, tool_use_id: str, text: str, exempt_ids: set[str] | None = None
    ) -> str:
        from kyncode.context.manager import (
            make_persisted_preview,
            persist_tool_result,
        )

        # 超过阈值的输出持久化到磁盘，对话里只保留预览和文件路径。
        # 溢写文件的回读结果豁免；写盘失败会原样保留，同一块磁盘
        # 聚合预算也不必再试，所以两种结果都标记豁免
        if exempt_ids is not None and tool_use_id in exempt_ids:
            return self._guard_readback(text)
        if estimate_text_tokens(text) > MAX_OUTPUT_TOKENS:
            before_tokens = estimate_text_tokens(text)
            try:
                fp = persist_tool_result(tool_use_id, text, self.session_dir)
            except OSError:
                # 写盘失败是静默降级：大结果没能外置，原样保留进历史。必须记下，
                # 否则「外置失败」永远不可观测。
                log.warning(
                    "spill %s",
                    json.dumps(
                        {
                            "event": "write_failed",
                            "tool_use_id": tool_use_id,
                            "tokens": before_tokens,
                        },
                        ensure_ascii=False,
                    ),
                )
                if exempt_ids is not None:
                    exempt_ids.add(tool_use_id)
                return text
            preview = make_persisted_preview(text, fp)
            log.info(
                "spill %s",
                json.dumps(
                    {
                        "event": "single",
                        "tool_use_id": tool_use_id,
                        "before_tokens": before_tokens,
                        "after_tokens": estimate_text_tokens(preview),
                    },
                    ensure_ascii=False,
                ),
            )
            if exempt_ids is not None:
                exempt_ids.add(tool_use_id)
            return preview
        return text

    def _guard_readback(self, text: str) -> str:
        """回读溢写文件时给结果加 token 上限。

        豁免落盘是为了让模型能看到读回的内容、避免「读回→溢写」死循环，
        但不等于无条件放行：超过 READBACK_TOKEN_LIMIT 就拒绝整体读回，
        引导模型改用 Grep 定向检索或 ReadFile 分块读取，防止一次读回
        超大文件把上下文窗口撑爆。
        """
        tokens = estimate_text_tokens(text)
        if tokens <= READBACK_TOKEN_LIMIT:
            return text
        return (
            f"[已阻止整体读回：该文件约 {tokens} tokens，远超安全上限"
            f"（{READBACK_TOKEN_LIMIT}），整体读入会撑爆上下文窗口。\n"
            f"请改用定向检索或分块读取，只取所需内容：\n"
            f"- Grep(pattern=\"关键词\", path=该文件路径) 定位相关行\n"
            f"- ReadFile(file_path=该文件路径, offset=行号, limit=行数) 读特定段落]"
        )

    async def manual_compact(
        self, conversation: ConversationManager
    ) -> CompactNotification | ErrorEvent:
        # auto_compact 会用摘要替换 conversation.history，所有 tool-result 内容
        # （原始或已替换的）都将被丢弃。这里跳过 apply_tool_result_budget —
        # 它在主循环中的唯一目的是为 LLM 调用生成 api_conv，而本路径不需要
        # 发起看到替换结果的 LLM 调用（auto_compact 内部的摘要调用操作的是原始对话）。
        result = await auto_compact(
            conversation,
            self.client,
            self.context_window,
            self.session_dir,
            protocol=self.protocol,
            manual=True,
            breaker=self.compact_breaker,
            recovery=self.recovery_state,
            tool_schemas=self.registry.get_all_schemas(self.protocol),
            transcript_path=self._transcript_path,
        )
        if isinstance(result, CompactEvent):
            env_context = build_environment_context(
                self.work_dir,
                self.active_skills,
                self._skill_catalog,
                self._agent_catalog,
            )
            conversation.inject_environment(env_context)
            memory_content = self.memory_manager.load() if self.memory_manager else ""
            conversation.inject_long_term_memory(
                self.instructions_content, memory_content
            )
            return CompactNotification(
                before_tokens=result.before_tokens,
                after_tokens=result.after_tokens,
                message=(
                    f"上下文已压缩（{result.before_tokens:,} → "
                    f"{result.after_tokens:,} tokens，回收 "
                    f"{result.before_tokens - result.after_tokens:,}）"
                ),
                boundary=result.boundary,
            )
        return ErrorEvent(message=result or "压缩失败：对话历史为空或未达到压缩条件")

    # ------------------------------------------------------------------
    # 运行时状态持久化：Session 只存对话，不存 agent 的运行时心智。
    # active_skills / recovery_state / file_state_cache 这些纯内存态崩溃即丢，
    # 这里把它们落盘到 per-session 的 state 文件，resume 时重建。
    # ------------------------------------------------------------------

    def runtime_state_path(self) -> Path:
        """运行时心智快照的落盘路径，与 session jsonl / ledger 并排。"""
        sid = self.session_id or "default"
        return Path(self.work_dir) / ".kyncode" / "sessions" / f"{sid}.state.json"

    def _file_state_cache(self) -> Any:
        """拿到与 ReadFile/EditFile/WriteFile 共享的那个 FileStateCache 实例。"""
        read_tool = self.registry.get("ReadFile")
        return getattr(read_tool, "_state_cache", None)

    def save_runtime_state(self) -> None:
        """把崩溃即丢的运行时心智落盘：激活的 skill、已读文件、技能快照。"""
        fsc = self._file_state_cache()
        data: dict[str, Any] = {
            "active_skills": dict(self.active_skills),
            "recovery_state": self.recovery_state.to_dict(),
            "file_state_cache": fsc.to_dict() if fsc is not None else {},
        }
        path = self.runtime_state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            # 落盘失败不阻断主流程：这只是「恢复能力」的增强，不是硬依赖。
            pass

    def load_runtime_state(self) -> None:
        """resume 时从磁盘重建运行时心智（active_skills / recovery_state / file_state_cache）。

        读不到文件或数据损坏时静默跳过，绝不让恢复动作本身把 resume 打断。
        """
        path = self.runtime_state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return

        skills = data.get("active_skills")
        if isinstance(skills, dict):
            self.active_skills = {str(k): str(v) for k, v in skills.items()}

        rs = data.get("recovery_state")
        if isinstance(rs, dict):
            self.recovery_state = RecoveryState.from_dict(rs)

        fsc_data = data.get("file_state_cache")
        fsc = self._file_state_cache()
        if isinstance(fsc_data, dict) and fsc is not None:
            fsc.load_dict(fsc_data)

    def build_resume_notice(self) -> str:
        """resume 时生成一条注入给模型的恢复提示。

        用 FileStateCache 的 mtime 判断哪些已读文件内容没变（历史里的读取结果
        仍有效、不必重读）、哪些变了（需重读），并列出仍激活的 skill。没有值得
        提示的内容时返回空串。
        """
        sections: list[str] = []

        fsc = self._file_state_cache()
        if fsc is not None:
            unchanged, changed = fsc.freshness()
            if unchanged or changed:
                buf = ["## 文件状态恢复"]
                if unchanged:
                    buf.append(
                        "以下文件在上次会话中读取过、内容未变化，历史对话里的读取结果"
                        "仍有效，无需重复读取："
                    )
                    buf.extend(f"- {p}" for p in unchanged)
                if changed:
                    buf.append(
                        "以下文件在上次会话中读取过、但之后被修改或删除，请重新读取最新内容："
                    )
                    buf.extend(f"- {p}" for p in changed)
                sections.append("\n".join(buf))

        if self.active_skills:
            buf = ["## 已激活的技能"]
            buf.append("以下技能在上次会话中已激活，其触发条件仍然适用：")
            buf.extend(f"- {name}" for name in self.active_skills)
            sections.append("\n".join(buf))

        return "\n\n".join(sections)

    async def run_to_completion(
        self,
        task: str,
        conversation: ConversationManager | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        # 和 run() 一样把 current_agent_id 绑定到本 agent，让子 agent 的工具
        # 调用日志（runtime 的 tool_call / permission_check）归属到它自己，
        # 而不是继承父 agent 的 ContextVar 值或落成 null。
        token = current_agent_id.set(self.agent_id)
        try:
            return await run_agent_loop_to_completion(
                self, task, conversation, event_callback
            )
        finally:
            current_agent_id.reset(token)

    async def execute_tool_noninteractive(self, tc: ToolCallComplete) -> ToolResult:
        if self.hook_engine:
            file_path = self._infer_file_path(tc.arguments)
            hook_ctx = self.build_hook_context(
                "pre_tool_use",
                tool_name=tc.tool_name,
                tool_args=tc.arguments,
                file_path=file_path,
            )
            rejection = await self.hook_engine.run_pre_tool_hooks(hook_ctx)
            if rejection is not None:
                return ToolResult(
                    output=f"Hook rejected: {rejection.reason}",
                    is_error=True,
                )

        invocation: ToolInvocation | None = None
        async for item in self.tool_runtime.invoke(tc, interactive=False):
            invocation = item
        assert invocation is not None
        if invocation.executed:
            self.tool_call_count += 1
        result = invocation.result

        if self.hook_engine:
            file_path = self._infer_file_path(tc.arguments)
            hook_ctx = self.build_hook_context(
                "post_tool_use",
                tool_name=tc.tool_name,
                tool_args=tc.arguments,
                file_path=file_path,
            )
            await self.hook_engine.run_hooks("post_tool_use", hook_ctx)

        return result
