"""ToolRuntime 三段管线（prepare → execute → finalize）的单元测试。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from kyncode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from kyncode.permissions.approval import (
    PermissionReply,
    PermissionRequest,
    PermissionResponse,
)
from kyncode.tools import ToolRegistry
from kyncode.tools.base import Tool, ToolCallComplete, ToolResult
from kyncode.tools.errors import ToolErrorCode
from kyncode.tools.runtime import ToolRuntime
from kyncode.tracing import current_agent_id


class _Params(BaseModel):
    value: int = 0


class _FakeTool(Tool):
    name = "FakeTool"
    description = "a fake read tool for tests"
    params_model = _Params
    category = "read"

    def __init__(self, fn: Callable[..., Any] | None = None) -> None:
        self._fn = fn

    async def execute(self, params: _Params) -> ToolResult:
        if self._fn is not None:
            return await self._fn(params)
        return ToolResult(output=f"value={params.value}")


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


async def _collect(invoke, *, reply: PermissionReply | None = None):
    """消费 invoke 生成器；PermissionRequest 用 reply（默认 ALLOW）答复。"""
    result = None
    requests: list[PermissionRequest] = []
    async for item in invoke:
        if isinstance(item, PermissionRequest):
            requests.append(item)
            item.future.set_result(reply or PermissionReply(PermissionResponse.ALLOW))
        else:
            result = item
    return result, requests


def _checker(mode: PermissionMode = PermissionMode.DEFAULT) -> PermissionChecker:
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox("."),
        rule_engine=RuleEngine(),
        mode=mode,
    )


# ---- prepare：查找 / 校验 / 权限 ----------------


@pytest.mark.asyncio
async def test_unknown_tool_short_circuits_in_prepare():
    runtime = ToolRuntime(_registry())
    tc = ToolCallComplete("t9", "Nope", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.is_error
    assert "unknown tool" in result.result.output
    assert result.executed is False
    assert result.tool_id == "t9"
    assert result.result.error_code == ToolErrorCode.UNKNOWN_TOOL


@pytest.mark.asyncio
async def test_validation_error_short_circuits_in_prepare():
    runtime = ToolRuntime(_registry(_FakeTool()))
    # value 应为 int，传字符串触发 ValidationError
    tc = ToolCallComplete("t1", "FakeTool", {"value": "not-an-int"})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.is_error
    assert "Parameter validation error" in result.result.output
    assert result.executed is False


@pytest.mark.asyncio
async def test_read_tool_executes_and_finalizes():
    runtime = ToolRuntime(_registry(_FakeTool()))
    tc = ToolCallComplete("t1", "FakeTool", {"value": 42})

    result, _ = await _collect(runtime.invoke(tc))
    assert not result.result.is_error
    assert result.result.output == "value=42"
    assert result.executed is True
    assert result.elapsed >= 0


@pytest.mark.asyncio
async def test_noninteractive_ask_denies_in_default_mode():
    checker = _checker(PermissionMode.DEFAULT)
    write_tool = _FakeTool()
    write_tool.category = "write"
    runtime = ToolRuntime(_registry(write_tool), permission_checker=checker)
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, requests = await _collect(runtime.invoke(tc, interactive=False))
    assert not requests
    assert result.result.is_error
    assert "non-interactive" in result.result.output
    assert result.executed is False


@pytest.mark.asyncio
async def test_noninteractive_ask_auto_allows_in_bypass():
    checker = _checker(PermissionMode.BYPASS)
    write_tool = _FakeTool()
    write_tool.category = "write"
    runtime = ToolRuntime(_registry(write_tool), permission_checker=checker)
    tc = ToolCallComplete("t1", "FakeTool", {"value": 7})

    result, requests = await _collect(runtime.invoke(tc, interactive=False))
    assert not requests
    assert not result.result.is_error
    assert result.executed is True


@pytest.mark.asyncio
async def test_interactive_ask_yields_permission_request_and_deny():
    checker = _checker(PermissionMode.DEFAULT)
    write_tool = _FakeTool()
    write_tool.category = "write"
    runtime = ToolRuntime(_registry(write_tool), permission_checker=checker)
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, requests = await _collect(
        runtime.invoke(tc, interactive=True),
        reply=PermissionReply(PermissionResponse.DENY, "别写"),
    )
    assert len(requests) == 1
    assert requests[0].tool_name == "FakeTool"
    assert result.result.is_error
    assert result.executed is False


@pytest.mark.asyncio
async def test_interactive_ask_allow_runs_tool():
    checker = _checker(PermissionMode.DEFAULT)
    write_tool = _FakeTool()
    write_tool.category = "write"
    runtime = ToolRuntime(_registry(write_tool), permission_checker=checker)
    tc = ToolCallComplete("t1", "FakeTool", {"value": 9})

    result, requests = await _collect(
        runtime.invoke(tc, interactive=True),
        reply=PermissionReply(PermissionResponse.ALLOW),
    )
    assert len(requests) == 1
    assert not result.result.is_error
    assert result.result.output == "value=9"
    assert result.executed is True


# ---- execute：超时 / 异常 / 重试 ----------------


@pytest.mark.asyncio
async def test_timeout_returns_error():
    async def slow(_params):
        await asyncio.sleep(10)
        return ToolResult(output="never")

    runtime = ToolRuntime(_registry(_FakeTool(slow)), timeout=0.01)
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.is_error
    assert "timed out" in result.result.output
    assert result.executed is True
    assert result.result.error_code == ToolErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_exception_wrapped_in_execute():
    async def boom(_params):
        raise RuntimeError("boom")

    runtime = ToolRuntime(_registry(_FakeTool(boom)))
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.is_error
    assert "Tool execution error" in result.result.output
    assert "boom" in result.result.output
    assert result.result.error_code == ToolErrorCode.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_retry_only_for_read_only_tools():
    calls: list[int] = []

    async def flaky(_params):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("transient")
        return ToolResult(output="recovered")

    read_tool = _FakeTool(flaky)
    runtime = ToolRuntime(_registry(read_tool), max_retries=2)
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert not result.result.is_error
    assert result.result.output == "recovered"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_read_only_tool_logic_error_not_retried():
    # 只读工具的逻辑错误（非可重试错误）不应重试：错误分类与只读守卫按 AND 组合。
    calls: list[int] = []

    async def flaky(_params):
        calls.append(1)
        raise RuntimeError("bug")

    read_tool = _FakeTool(flaky)
    runtime = ToolRuntime(_registry(read_tool), max_retries=2)
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.is_error
    assert len(calls) == 1  # 逻辑错误立即失败，不重试


@pytest.mark.asyncio
async def test_write_tool_is_not_retried():
    calls: list[int] = []

    async def flaky(_params):
        calls.append(1)
        raise RuntimeError("boom")

    write_tool = _FakeTool(flaky)
    write_tool.category = "write"
    runtime = ToolRuntime(
        _registry(write_tool),
        permission_checker=_checker(PermissionMode.BYPASS),
        max_retries=2,
    )
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.is_error
    assert len(calls) == 1  # 写工具不重试，避免重复副作用


# ---- finalize：tool_id / 错误码 / Output Model / 脱敏 / 体积 / trace ----

# 声明了 output_model 的工具：execute 返回 JSON，finalize 会校验并投影。
class _OutModel(BaseModel):
    name: str
    count: int
    optional: str | None = None


class _StructuredTool(Tool):
    name = "StructuredTool"
    description = "returns structured JSON"
    params_model = _Params
    category = "read"
    output_model = _OutModel

    def __init__(self, output: str) -> None:
        self._output = output

    async def execute(self, params: _Params) -> ToolResult:
        return ToolResult(output=self._output)


@pytest.mark.asyncio
async def test_finalize_preserves_tool_call_id():
    runtime = ToolRuntime(_registry(_FakeTool()))
    tc = ToolCallComplete("call-123", "FakeTool", {"value": 1})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.tool_id == "call-123"


@pytest.mark.asyncio
async def test_output_model_validates_and_projects():
    tool = _StructuredTool('{"name": "x", "count": 3, "extra": "drop", "optional": null}')
    runtime = ToolRuntime(_registry(tool))
    tc = ToolCallComplete("t1", "StructuredTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert not result.result.is_error
    data = json.loads(result.result.output)
    # extra 不在 output_model 里被丢弃；optional 为 None 被投影时剔除。
    assert data == {"name": "x", "count": 3}


@pytest.mark.asyncio
async def test_output_model_invalid_output_maps_error_code():
    tool = _StructuredTool("not-json")
    runtime = ToolRuntime(_registry(tool))
    tc = ToolCallComplete("t1", "StructuredTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.is_error
    assert result.result.error_code == ToolErrorCode.INVALID_OUTPUT


@pytest.mark.asyncio
async def test_finalize_redacts_sensitive():
    async def leak(_params):
        return ToolResult(
            output="token sk-abcdef1234567890 mail a@b.com phone 13812345678"
        )

    runtime = ToolRuntime(_registry(_FakeTool(leak)))
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert "[REDACTED_TOKEN]" in result.result.output
    assert "[REDACTED_EMAIL]" in result.result.output
    assert "[REDACTED_PHONE]" in result.result.output


@pytest.mark.asyncio
async def test_finalize_emits_trace_with_tool_call_id(caplog):
    runtime = ToolRuntime(_registry(_FakeTool()))
    tc = ToolCallComplete("call-42", "FakeTool", {"value": 5})

    token = current_agent_id.set("agent-abc")
    try:
        with caplog.at_level(logging.DEBUG, logger="kyncode.tools.runtime"):
            await _collect(runtime.invoke(tc))
    finally:
        current_agent_id.reset(token)

    trace = next(r for r in caplog.records if "tool_call" in r.message)
    # 结构化 payload 里应带上原始 tool_call_id 与当前 agent id。
    assert "call-42" in trace.message
    assert '"agent_id": "agent-abc"' in trace.message


@pytest.mark.asyncio
async def test_permission_decision_audits_deny(caplog):
    checker = _checker(PermissionMode.DEFAULT)
    write_tool = _FakeTool()
    write_tool.category = "write"
    runtime = ToolRuntime(_registry(write_tool), permission_checker=checker)
    tc = ToolCallComplete("call-9", "FakeTool", {})

    with caplog.at_level(logging.DEBUG, logger="kyncode.tools.runtime"):
        result, requests = await _collect(
            runtime.invoke(tc, interactive=True),
            reply=PermissionReply(PermissionResponse.DENY, feedback="换个做法"),
        )

    assert result.result.is_error
    assert len(requests) == 1
    decisions = [r for r in caplog.records if "permission_decision" in r.message]
    assert len(decisions) == 1
    assert '"decision": "deny"' in decisions[0].message
    assert "换个做法" in decisions[0].message


@pytest.mark.asyncio
async def test_permission_decision_audits_allow_always(caplog):
    checker = _checker(PermissionMode.DEFAULT)
    write_tool = _FakeTool()
    write_tool.category = "write"
    runtime = ToolRuntime(_registry(write_tool), permission_checker=checker)
    tc = ToolCallComplete("call-10", "FakeTool", {"value": 1})

    with caplog.at_level(logging.DEBUG, logger="kyncode.tools.runtime"):
        result, requests = await _collect(
            runtime.invoke(tc, interactive=True),
            reply=PermissionReply(PermissionResponse.ALLOW_ALWAYS),
        )

    assert not result.result.is_error
    assert len(requests) == 1
    decisions = [r for r in caplog.records if "permission_decision" in r.message]
    assert len(decisions) == 1
    assert '"decision": "allow_always"' in decisions[0].message
    assert '"rule_pattern"' in decisions[0].message


# ---- repeat guard：防止同一工具同一参数连续重复调用 ----


@pytest.mark.asyncio
async def test_repeat_blocks_third_consecutive_call():
    calls: list[int] = []

    async def record(_params):
        calls.append(1)
        return ToolResult(output="ran")

    runtime = ToolRuntime(_registry(_FakeTool(record)))
    tc = ToolCallComplete("t1", "FakeTool", {"value": 1})

    r1, _ = await _collect(runtime.invoke(tc))
    r2, _ = await _collect(runtime.invoke(tc))
    r3, _ = await _collect(runtime.invoke(tc))

    # 前 2 次真实执行，第 3 次连续重复被拦截。
    assert not r1.result.is_error
    assert not r2.result.is_error
    assert r3.result.is_error
    assert r3.result.error_code == ToolErrorCode.REPEATED_CALL
    assert r3.executed is False
    assert "FakeTool" in r3.result.output
    assert len(calls) == 2  # 第 3 次没有真正执行


@pytest.mark.asyncio
async def test_repeat_resets_on_different_arguments():
    calls: list[int] = []

    async def record(_params):
        calls.append(1)
        return ToolResult(output="ran")

    runtime = ToolRuntime(_registry(_FakeTool(record)))
    tc_same = ToolCallComplete("t1", "FakeTool", {"value": 1})

    await _collect(runtime.invoke(tc_same))
    await _collect(runtime.invoke(tc_same))
    # 换参数 → 指纹变化 → 连续计数重置，不应被拦截。
    tc_diff = ToolCallComplete("t2", "FakeTool", {"value": 2})
    r3, _ = await _collect(runtime.invoke(tc_diff))

    assert not r3.result.is_error
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_repeat_resets_on_different_tool():
    calls: list[int] = []

    async def record(_params):
        calls.append(1)
        return ToolResult(output="ran")

    fake = _FakeTool(record)
    other = _StructuredTool('{"name": "x", "count": 1}')
    runtime = ToolRuntime(_registry(fake, other))

    tc = ToolCallComplete("t1", "FakeTool", {})
    await _collect(runtime.invoke(tc))
    await _collect(runtime.invoke(tc))
    # 中间换一个工具 → 连续计数重置。
    await _collect(runtime.invoke(ToolCallComplete("t2", "StructuredTool", {})))
    # 回到 FakeTool，应从 1 重新计数，不拦截。
    r, _ = await _collect(runtime.invoke(tc))

    assert not r.result.is_error
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_repeat_threshold_configurable():
    calls: list[int] = []

    async def record(_params):
        calls.append(1)
        return ToolResult(output="ran")

    runtime = ToolRuntime(_registry(_FakeTool(record)), repeat_threshold=2)
    tc = ToolCallComplete("t1", "FakeTool", {"value": 1})

    r1, _ = await _collect(runtime.invoke(tc))
    r2, _ = await _collect(runtime.invoke(tc))

    assert not r1.result.is_error
    assert r2.result.is_error
    assert r2.result.error_code == ToolErrorCode.REPEATED_CALL
    assert len(calls) == 1

