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
    tc = ToolCallComplete("t1", "Nope", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.is_error
    assert "unknown tool" in result.result.output
    assert result.executed is False


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


@pytest.mark.asyncio
async def test_retry_only_for_read_only_tools():
    calls: list[int] = []

    async def flaky(_params):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")
        return ToolResult(output="recovered")

    read_tool = _FakeTool(flaky)
    runtime = ToolRuntime(_registry(read_tool), max_retries=2)
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert not result.result.is_error
    assert result.result.output == "recovered"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_write_tool_is_not_retried():
    calls: list[int] = []

    async def flaky(_params):
        calls.append(1)
        raise RuntimeError("boom")

    write_tool = _FakeTool(flaky)
    write_tool.category = "write"
    runtime = ToolRuntime(_registry(write_tool), max_retries=2)
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
async def test_short_circuit_carries_tool_id_and_error_code():
    runtime = ToolRuntime(_registry())
    tc = ToolCallComplete("t9", "Nope", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.tool_id == "t9"
    assert result.executed is False
    assert result.result.error_code == ToolErrorCode.UNKNOWN_TOOL


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
async def test_finalize_limits_size_and_marks_truncated():
    async def big(_params):
        return ToolResult(output="x" * 100)

    runtime = ToolRuntime(_registry(_FakeTool(big)), max_output_chars=10)
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.truncated is True
    assert len(result.result.output) < 100


@pytest.mark.asyncio
async def test_timeout_maps_to_stable_error_code():
    async def slow(_params):
        await asyncio.sleep(10)
        return ToolResult(output="never")

    runtime = ToolRuntime(_registry(_FakeTool(slow)), timeout=0.01)
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.error_code == ToolErrorCode.TIMEOUT


@pytest.mark.asyncio
async def test_exception_maps_to_execution_error_code():
    async def boom(_params):
        raise RuntimeError("boom")

    runtime = ToolRuntime(_registry(_FakeTool(boom)))
    tc = ToolCallComplete("t1", "FakeTool", {})

    result, _ = await _collect(runtime.invoke(tc))
    assert result.result.error_code == ToolErrorCode.EXECUTION_ERROR


@pytest.mark.asyncio
async def test_finalize_emits_trace_with_tool_call_id(caplog):
    runtime = ToolRuntime(_registry(_FakeTool()))
    tc = ToolCallComplete("call-42", "FakeTool", {"value": 5})

    with caplog.at_level(logging.DEBUG, logger="kyncode.tools.runtime"):
        await _collect(runtime.invoke(tc))

    assert any("tool_call" in r.message for r in caplog.records)
    # 结构化 payload 里应带上原始 tool_call_id。
    assert any("call-42" in r.message for r in caplog.records)

