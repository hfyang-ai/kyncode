from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from kyncode.conversation_pairing import rejected_tool_result
from kyncode.permissions import Decision, PermissionChecker, PermissionMode
from kyncode.permissions.approval import (
    PermissionReply,
    PermissionRequest,
    PermissionResponse,
)
from kyncode.tools import ToolRegistry
from kyncode.tools.base import Tool, ToolCallComplete, ToolResult
from kyncode.tools.errors import ToolErrorCode
from kyncode.tools.ledger import (
    DENIED,
    FAILED,
    REPLAYABLE_STATUSES,
    SUCCEEDED,
    LedgerEntry,
    ToolLedger,
    fingerprint,
)
from kyncode.tools.redact import redact_sensitive
from kyncode.tracing import (
    TRACE_ARGS_LIMIT,
    TRACE_OUTPUT_LIMIT,
    truncate,
)

log = logging.getLogger(__name__)


@dataclass
class PreparedCall:
    """prepare 阶段的产物：工具定义已找到、参数已通过 Schema 校验。"""

    tool_call: ToolCallComplete
    tool: Tool
    params: BaseModel


@dataclass
class _Execution:
    """execute 阶段的产物：成功时带 result，失败时保留原始异常与预判错误码。

    finalize 不直接面对「异常对象」，而是从这里的 exception / error_code 里
    把原始异常映射成稳定的错误码，这样错误码的产出集中在 finalize 一处。
    """

    result: ToolResult | None = None
    exception: BaseException | None = None
    error_code: ToolErrorCode | None = None


@dataclass
class ToolInvocation:
    """invoke 的最终产物：封装好的工具结果 + 本次调用耗时。

    tool_id 恒为原始 tool_call_id，保证这条结果能配上对应的 tool_use 块。
    executed 表示是否真的走到了 execute 阶段；unknown / disabled / 权限拒绝
    这类短路返回时为 False，供上层决定要不要做「最近工具名」之类的副作用登记。
    """

    result: ToolResult
    elapsed: float
    tool_id: str
    executed: bool = False


class ToolRuntime:
    """把一次工具调用走完「prepare → execute → finalize」三段管线。

    管线对应流程：

        解析工具名称与参数 → 查找工具定义 → 校验参数 Schema → 检查权限与审批
        → 执行工具 → 处理超时、异常与重试 → 封装 ToolResult → 交回 Agent Loop

    finalize 阶段统一负责结果封装：按 Output Model 校验并投影、脱敏敏感信息、
    限制结果体积、把异常映射成稳定错误码、写入 trace / 审计日志，并保留原始
    tool_call_id。所有出口（含 prepare 短路与权限拒绝）都经过 finalize，保证
    上述后处理对成功与失败结果一视同仁。

    interactive=True 时，遇到需要审批（effect=="ask"）的调用会 yield 一个
    PermissionRequest 交给上层等待用户决定；否则按非交互处理：BYPASS 模式自动
    放行，其余拒绝。

    timeout / max_retries 默认关闭（不超时、不重试），行为与重构前一致；重试
    只对只读工具生效，避免对非幂等工具重复执行产生重复副作用。max_output_chars
    默认关闭（不截断），打开时 finalize 会把超长输出截断并标记 truncated。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        permission_checker: PermissionChecker | None = None,
        timeout: float | None = None,
        max_retries: int = 0,
        max_output_chars: int | None = None,
        ledger: ToolLedger | None = None,
        repeat_threshold: int = 3,
    ) -> None:
        self.registry = registry
        self.permission_checker = permission_checker
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_output_chars = max_output_chars
        # 执行轨迹账本：为 None 时不记录、不重放（一次性调用、子 agent 等）。
        # 传入后，非幂等工具执行前落 executing、终态落 outcome，invoke 顶部
        # 先查账本重放已提交的结果，避免恢复会话后重复副作用。
        self.ledger = ledger
        # 防死循环：同一工具 + 等价参数连续调用达到 repeat_threshold 次即拦截。
        # 默认 3 —— 前 2 次真实执行，第 3 次连续重复直接返回错误、不真正执行。
        self.repeat_threshold = repeat_threshold
        # 「连续重复」追踪：记上一个调用的（工具名, 内容指纹）与连续计数。
        # 换工具或换参数都会重置计数，不误伤合法的「改参重试」。
        self._repeat_key: tuple[str, str] | None = None
        self._repeat_count = 0

    async def invoke(
        self, tool_call: ToolCallComplete, *, interactive: bool = False
    ) -> AsyncIterator[PermissionRequest | ToolInvocation]:
        start = time.monotonic()
        tc = tool_call

        # ── replay：恢复会话后拦截「已经执行过」的非幂等调用 ──
        # 先按 tool_call_id 查（同一 tool_use 被重放），查不到再按内容指纹查
        # （模型换个新 id 重新发起同一动作）。命中就交回记账结果，绝不重跑。
        replayed = self._maybe_replay(tc)
        if replayed is not None:
            yield self._finalize(
                replayed,
                tc,
                tool=self.registry.get(tc.tool_name),
                start=start,
                executed=False,
                record=False,
            )
            return

        # ── prepare：解析 / 查找 / 校验 / 权限与审批 ──
        prepared = self._prepare(tc)
        if isinstance(prepared, ToolResult):
            yield self._finalize(prepared, tc, tool=None, start=start, executed=False)
            return

        decision = self._check_permission(prepared)
        if decision is not None and decision.effect == "deny":
            yield self._finalize(
                ToolResult(
                    output=f"Permission denied: {decision.reason}",
                    is_error=True,
                    error_code=ToolErrorCode.PERMISSION_DENIED,
                ),
                tc,
                tool=prepared.tool,
                start=start,
                executed=False,
            )
            return

        if decision is not None and decision.effect == "ask":
            if interactive:
                request = self._make_request(prepared)
                yield request
                reply = await request.future
                if reply.response is PermissionResponse.DENY:
                    yield self._finalize(
                        ToolResult(
                            output=rejected_tool_result(reply.feedback),
                            is_error=True,
                            error_code=ToolErrorCode.PERMISSION_DENIED,
                        ),
                        tc,
                        tool=prepared.tool,
                        start=start,
                        executed=False,
                    )
                    return
                if reply.response is PermissionResponse.ALLOW_ALWAYS:
                    self._remember_allow(prepared)
                # ALLOW 落到下面执行
            else:
                if self._current_mode() is not PermissionMode.BYPASS:
                    yield self._finalize(
                        ToolResult(
                            output=(
                                "Permission denied: non-interactive agent "
                                "cannot prompt user"
                            ),
                            is_error=True,
                            error_code=ToolErrorCode.PERMISSION_DENIED,
                        ),
                        tc,
                        tool=prepared.tool,
                        start=start,
                        executed=False,
                    )
                    return
                # BYPASS 自动放行，落到下面执行

        # ── execute：执行 + 超时 / 异常 / 重试 ──
        # repeat guard：同一工具 + 等价参数连续重复调用达阈值即拦截。放在这里
        # （prepare 校验、权限审批都通过之后、真正执行之前），只防「会真正执行」
        # 的重复——unknown tool / 参数错误 / 权限拒绝这类短路不产生副作用，不计数。
        repeated = self._check_repeat(tc)
        if repeated is not None:
            yield self._finalize(
                repeated,
                tc,
                tool=prepared.tool,
                start=start,
                executed=False,
                record=False,
            )
            return

        # 执行前先把「即将执行」落到账本：若执行中途进程崩了，恢复时能凭这条
        # executing（无终态）认出「结局未知」，而不是误判成「从没跑过」。
        self._record_executing(prepared)
        execution = await self._execute(prepared)

        # ── finalize：封装结果 ──
        yield self._finalize(
            execution.result
            if execution.result is not None
            else self._exception_to_result(execution),
            tc,
            tool=prepared.tool,
            start=start,
            executed=True,
        )

    # ---- prepare ----

    def _prepare(self, tool_call: ToolCallComplete) -> PreparedCall | ToolResult:
        tool = self.registry.get(tool_call.tool_name)
        if tool is None:
            return ToolResult(
                output=f"Error: unknown tool '{tool_call.tool_name}'",
                is_error=True,
                error_code=ToolErrorCode.UNKNOWN_TOOL,
            )
        if not self.registry.is_enabled(tool_call.tool_name):
            return ToolResult(
                output=f"Error: tool '{tool_call.tool_name}' is disabled",
                is_error=True,
                error_code=ToolErrorCode.TOOL_DISABLED,
            )
        try:
            params = tool.params_model.model_validate(tool_call.arguments)
        except ValidationError as e:
            return ToolResult(
                output=f"Parameter validation error: {e}",
                is_error=True,
                error_code=ToolErrorCode.INVALID_ARGUMENTS,
            )
        return PreparedCall(tool_call=tool_call, tool=tool, params=params)

    def _check_permission(self, prepared: PreparedCall) -> Decision | None:
        if self.permission_checker is None:
            # fail-closed：无 checker 时只放行只读工具，写/命令类一律拒绝。
            # 防止未来新增 Agent 实例化点忘记传 checker 时静默裸奔。
            if prepared.tool.category == "read":
                return None
            return Decision(
                effect="deny",
                reason="no permission checker configured (fail-closed)",
            )
        return self.permission_checker.check(
            prepared.tool, prepared.tool_call.arguments
        )

    def _current_mode(self) -> PermissionMode:
        if self.permission_checker is None:
            return PermissionMode.DEFAULT
        return self.permission_checker.mode

    def _make_request(self, prepared: PreparedCall) -> PermissionRequest:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[PermissionReply] = loop.create_future()
        description = PermissionChecker.describe_tool_action(
            prepared.tool_call.tool_name, prepared.tool_call.arguments
        )
        return PermissionRequest(
            tool_name=prepared.tool_call.tool_name,
            description=description,
            future=future,
        )

    def _remember_allow(self, prepared: PreparedCall) -> None:
        from kyncode.permissions.rules import Rule, extract_content

        if self.permission_checker is None:
            return
        content = extract_content(
            prepared.tool_call.tool_name, prepared.tool_call.arguments
        )
        pattern = f"{content[:60]}*" if len(content) > 60 else f"{content}*"
        rule = Rule(
            tool_name=prepared.tool_call.tool_name, pattern=pattern, effect="allow"
        )
        self.permission_checker.rule_engine.append_local_rule(rule)

    # ---- execute ----

    async def _execute(self, prepared: PreparedCall) -> _Execution:
        attempts = self.max_retries + 1
        last: _Execution | None = None
        for attempt in range(attempts):
            try:
                if self.timeout is not None:
                    result = await asyncio.wait_for(
                        prepared.tool.execute(prepared.params), timeout=self.timeout
                    )
                else:
                    result = await prepared.tool.execute(prepared.params)
                return _Execution(result=result)
            except TimeoutError as e:
                last = _Execution(exception=e, error_code=ToolErrorCode.TIMEOUT)
            except Exception as e:  # noqa: BLE001 — 工具异常收成结果，不打断循环
                last = _Execution(exception=e, error_code=ToolErrorCode.EXECUTION_ERROR)
            if not self._should_retry(prepared, attempt):
                break
        assert last is not None
        return last

    def _should_retry(self, prepared: PreparedCall, attempt: int) -> bool:
        # 只对只读工具重试：写/命令类工具重试可能重复产生副作用。
        return attempt < self.max_retries and prepared.tool.is_read_only

    # ---- repeat guard：防止同一工具同一参数连续重复调用 ----

    def _check_repeat(self, tc: ToolCallComplete) -> ToolResult | None:
        """防死循环的闸：同一工具 + 等价参数连续调用达阈值即拦截。

        由 invoke 在 prepare 校验、权限审批都通过之后、真正执行之前调用，因此只
        对「即将真正执行」的调用计数——unknown tool / 参数错误 / 权限拒绝这类短路
        不产生副作用，不进入本计数。

        维护「上一个调用的键 + 连续计数」：当前键与上一个相同则计数 +1，否则
        重置为 1。计数达到 repeat_threshold（默认 3，即前 2 次放行、第 3 次拦截）
        时返回一条 is_error 结果，不真正执行，逼模型停下重试、换思路。

        换工具或换参数都会重置计数（fingerprint 对参数顺序不敏感，只对内容敏感），
        因此合法的「改参重试」不会误伤。
        """
        key = (tc.tool_name, fingerprint(tc.tool_name, tc.arguments))
        if key == self._repeat_key:
            self._repeat_count += 1
        else:
            self._repeat_key = key
            self._repeat_count = 1
        if self._repeat_count >= self.repeat_threshold:
            return ToolResult(
                output=(
                    f"Repeated call blocked: tool '{tc.tool_name}' with identical "
                    f"arguments was called {self._repeat_count} times in a row. "
                    "This invocation was NOT executed. Stop repeating the same "
                    "call and reconsider your approach."
                ),
                is_error=True,
                error_code=ToolErrorCode.REPEATED_CALL,
            )
        return None

    # ---- ledger：执行轨迹记录与重放 ----

    @staticmethod
    def _replayable(tool: Tool | None) -> bool:
        """只有非幂等（会改东西）的工具才需要记账和重放。

        只读工具重跑无副作用，重放它们反而可能回读过期的文件内容。
        """
        return tool is not None and not tool.is_read_only

    def _maybe_replay(self, tc: ToolCallComplete) -> ToolResult | None:
        """恢复会话后，若这次调用已经执行过，交回记账结果而不是重跑。

        先按 tool_call_id 查（同一 tool_use 重放），再按内容指纹查（模型换新 id
        重新发起同一动作）。后者命中即消费，一次恢复只重放一次。返回 None 表示
        没有可重放的记录，走正常执行路径。
        """
        if self.ledger is None:
            return None
        tool = self.registry.get(tc.tool_name)
        if not self._replayable(tool):
            return None

        entry = self.ledger.committed_by_id.get(tc.tool_id)
        if entry is None:
            entry = self.ledger.consume_replay(tc.tool_name, tc.arguments)
        if entry is None or entry.result is None:
            return None
        # 只重放「真正执行过」的终态（succeeded/failed）。denied 意味着上次根本没
        # 执行、无副作用，不该被重放——让调用走正常权限检查，用户可能这次改主意放行。
        if entry.status not in REPLAYABLE_STATUSES:
            return None
        return self._result_from_entry(entry)

    @staticmethod
    def _result_from_entry(entry: LedgerEntry) -> ToolResult:
        """把账本里的 result dict 还原成 ToolResult。"""
        result = entry.result or {}
        return ToolResult(
            output=str(result.get("output", "")),
            is_error=bool(result.get("is_error", False)),
            error_code=result.get("error_code"),
            truncated=bool(result.get("truncated", False)),
        )

    def _record_executing(self, prepared: PreparedCall) -> None:
        if self.ledger is None or not self._replayable(prepared.tool):
            return
        self.ledger.record_executing(
            prepared.tool_call.tool_id,
            prepared.tool_call.tool_name,
            prepared.tool_call.arguments,
        )

    def _record_outcome(
        self,
        tool_call: ToolCallComplete,
        result: ToolResult,
        tool: Tool | None,
        executed: bool,
        elapsed_ms: float,
    ) -> None:
        if self.ledger is None or not self._replayable(tool):
            return
        if not executed:
            status = DENIED
        elif result.is_error:
            status = FAILED
        else:
            status = SUCCEEDED
        self.ledger.record_outcome(
            tool_call.tool_id,
            tool_call.tool_name,
            tool_call.arguments,
            result={
                "output": result.output,
                "is_error": result.is_error,
                "error_code": result.error_code,
                "truncated": result.truncated,
            },
            status=status,
            elapsed_ms=elapsed_ms,
        )

    # ---- finalize ----

    def _finalize(
        self,
        result: ToolResult,
        tool_call: ToolCallComplete,
        *,
        tool: Tool | None,
        start: float,
        executed: bool,
        record: bool = True,
    ) -> ToolInvocation:
        # 1. 错误码归一：工具自身返回 is_error 但没标 error_code 时兜底归类。
        if result.is_error and result.error_code is None:
            result.error_code = ToolErrorCode.EXECUTION_ERROR

        # 2. 按 Output Model 校验并投影（仅真实执行且工具声明了 output_model）。
        if executed and tool is not None:
            result = self._validate_and_project(tool, result)

        # 3. 脱敏 Token / 邮箱 / 密钥 / 个人数据。
        if result.output:
            result.output = redact_sensitive(result.output)

        # 4. 限制结果体积。
        result = self._limit_size(result)

        # 5. 写入 trace / 审计。
        self._emit_trace(tool_call, result, start, executed, tool)

        # 6. 记账：非幂等工具把终态落进执行轨迹账本（脱敏/截断之后的最终结果）。
        # 重放路径（record=False）不落账——重放的结果本就是账本里已有的终态，
        # 再记一条只会把原终态覆盖成一条假的 denied。
        if record:
            self._record_outcome(
                tool_call, result, tool, executed, (time.monotonic() - start) * 1000
            )

        # 7. 保留原始 tool_call_id。
        return ToolInvocation(
            result=result,
            elapsed=time.monotonic() - start,
            tool_id=tool_call.tool_id,
            executed=executed,
        )

    def _validate_and_project(self, tool: Tool, result: ToolResult) -> ToolResult:
        """按 output_model 校验输出，并只投影声明字段（收成紧凑 JSON）。

        未声明 output_model 或结果是错误时原样返回，不做任何改写。
        """
        model = tool.output_model
        if model is None or result.is_error:
            return result
        try:
            data = model.model_validate_json(result.output)
        except (ValidationError, ValueError) as e:
            return ToolResult(
                output=f"Tool output validation error: {e}",
                is_error=True,
                error_code=ToolErrorCode.INVALID_OUTPUT,
            )
        result.output = json.dumps(
            data.model_dump(exclude_none=True), ensure_ascii=False
        )
        return result

    def _limit_size(self, result: ToolResult) -> ToolResult:
        if self.max_output_chars is None or len(result.output) <= self.max_output_chars:
            return result
        result.output = result.output[: self.max_output_chars]
        result.output += f"\n...[truncated to {self.max_output_chars} chars]"
        result.truncated = True
        return result

    def _emit_trace(
        self,
        tool_call: ToolCallComplete,
        result: ToolResult,
        start: float,
        executed: bool,
        tool: Tool | None,
    ) -> None:
        """写一条结构化 trace，包含调用参数与结果内容（脱敏、截断）。

        统一落到 INFO：debug.log 的 root level 是 INFO，DEBUG 进不了文件。
        用户要从日志里看到工具调用的参数和输出，就必须记到 INFO 及以上。
        result.output 在 _finalize 里已脱敏，这里只再截断；arguments 在此脱敏。
        """
        payload = {
            "tool_call_id": tool_call.tool_id,
            "tool_name": tool_call.tool_name,
            "category": tool.category if tool is not None else None,
            "error_code": result.error_code,
            "is_error": result.is_error,
            "truncated": result.truncated,
            "executed": executed,
            "elapsed_ms": round((time.monotonic() - start) * 1000, 2),
            "arguments": truncate(
                json.dumps(tool_call.arguments, ensure_ascii=False, default=str),
                TRACE_ARGS_LIMIT,
            ),
            "output": truncate(result.output, TRACE_OUTPUT_LIMIT),
        }
        log.info(
            "tool_call %s",
            redact_sensitive(json.dumps(payload, ensure_ascii=False, default=str)),
        )

    def _exception_to_result(self, execution: _Execution) -> ToolResult:
        """把 execute 捕获的原始异常映射成稳定错误码 + 人读文案。"""
        code = execution.error_code or ToolErrorCode.EXECUTION_ERROR
        if code is ToolErrorCode.TIMEOUT:
            output = f"Tool timed out after {self.timeout}s"
        else:
            output = f"Tool execution error: {execution.exception}"
        return ToolResult(output=output, is_error=True, error_code=code)
