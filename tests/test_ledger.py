"""执行轨迹 checkpoint（ToolLedger）与 ToolRuntime 重放的单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from kyncode.conversation import Message, ToolUseBlock
from kyncode.conversation_pairing import (
    REPLAYED_TOOL_RESULT_PREFIX,
    resolve_dangling_tool_uses,
)
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
from kyncode.tools.ledger import DENIED, SUCCEEDED, ToolLedger, fingerprint
from kyncode.tools.runtime import ToolRuntime


class _Params(BaseModel):
    value: int = 0


class _CounterTool(Tool):
    """带副作用计数的工具，用来断言「真的执行了几次」。"""

    name = "CounterTool"
    description = "a non-idempotent tool for tests"
    params_model = _Params
    category = "write"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, params: _Params) -> ToolResult:
        self.calls.append({"value": params.value})
        return ToolResult(output=f"wrote={params.value}")


def _registry(*tools: Tool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _checker(mode: PermissionMode = PermissionMode.BYPASS) -> PermissionChecker:
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox("."),
        rule_engine=RuleEngine(),
        mode=mode,
    )


async def _collect(invoke):
    result = None
    async for item in invoke:
        if isinstance(item, PermissionRequest):
            item.future.set_result(PermissionReply(PermissionResponse.ALLOW))
        else:
            result = item
    return result


# ---- fingerprint ----------------


def test_fingerprint_is_deterministic_and_order_insensitive():
    a = fingerprint("Bash", {"command": "rm x", "timeout": 5})
    b = fingerprint("Bash", {"timeout": 5, "command": "rm x"})
    assert a == b
    assert fingerprint("Bash", {"command": "rm y"}) != a
    assert fingerprint("WriteFile", {"command": "rm x"}) != a


# ---- ToolLedger 两段写 / load 归并 ----------------


def test_ledger_round_trip_and_latest_status_wins(tmp_path: Path):
    path = tmp_path / "sessions" / "s1.ledger.jsonl"
    ledger = ToolLedger(path)

    ledger.record_executing("t1", "CounterTool", {"value": 1})
    ledger.record_outcome(
        "t1",
        "CounterTool",
        {"value": 1},
        result={"output": "wrote=1", "is_error": False, "error_code": None, "truncated": False},
        status=SUCCEEDED,
        elapsed_ms=1.0,
    )

    ledger2 = ToolLedger(path)
    ledger2.load()
    assert ledger2.committed_by_id["t1"].status == SUCCEEDED
    assert ledger2.committed_by_id["t1"].result["output"] == "wrote=1"
    # executing 没有终态 → 不该进 committed_by_id / 重放池
    assert "t1" not in ledger2.interrupted_ids()


def test_interrupted_only_executing_is_detected(tmp_path: Path):
    path = tmp_path / "sessions" / "s2.ledger.jsonl"
    ledger = ToolLedger(path)
    ledger.record_executing("t1", "CounterTool", {"value": 1})

    ledger2 = ToolLedger(path)
    ledger2.load()
    assert "t1" not in ledger2.committed_by_id  # 无终态
    assert "t1" in ledger2.interrupted_ids()  # 只有 executing


def test_replay_pool_excludes_denied_and_executing(tmp_path: Path):
    path = tmp_path / "sessions" / "s3.ledger.jsonl"
    ledger = ToolLedger(path)
    ledger.record_executing("t1", "CounterTool", {"value": 1})
    ledger.record_outcome(
        "t1", "CounterTool", {"value": 1},
        result={"output": "wrote=1", "is_error": False, "error_code": None, "truncated": False},
        status=SUCCEEDED, elapsed_ms=0.0,
    )
    # denied 终态：无副作用，不进重放池
    ledger.record_outcome(
        "t2", "CounterTool", {"value": 2},
        result={"output": "Permission denied", "is_error": True, "error_code": "permission_denied", "truncated": False},
        status=DENIED, elapsed_ms=0.0,
    )

    ledger2 = ToolLedger(path)
    ledger2.load()
    fp_ok = fingerprint("CounterTool", {"value": 1})
    fp_denied = fingerprint("CounterTool", {"value": 2})
    assert ledger2.consume_replay("CounterTool", {"value": 1}) is not None
    # denied 不在池里，consume 返回 None
    assert ledger2.consume_replay("CounterTool", {"value": 2}) is None
    # succeeded 的已消费
    assert fp_ok not in ledger2._replay_pool
    assert fp_denied not in ledger2._replay_pool


# ---- ToolRuntime：记账 ----------------


@pytest.mark.asyncio
async def test_runtime_records_executing_and_outcome(tmp_path: Path):
    path = tmp_path / "sessions" / "s4.ledger.jsonl"
    ledger = ToolLedger(path)
    tool = _CounterTool()
    runtime = ToolRuntime(
        _registry(tool), permission_checker=_checker(), ledger=ledger
    )
    tc = ToolCallComplete("t1", "CounterTool", {"value": 7})

    result = await _collect(runtime.invoke(tc))
    assert result.result.output == "wrote=7"

    ledger2 = ToolLedger(path)
    ledger2.load()
    assert ledger2.committed_by_id["t1"].status == SUCCEEDED
    assert ledger2.committed_by_id["t1"].result["output"] == "wrote=7"


@pytest.mark.asyncio
async def test_read_tool_is_not_journaled(tmp_path: Path):
    path = tmp_path / "sessions" / "s5.ledger.jsonl"
    ledger = ToolLedger(path)

    class _ReadTool(Tool):
        name = "ReadTool"
        description = "read"
        params_model = _Params
        category = "read"

        async def execute(self, params: _Params) -> ToolResult:
            return ToolResult(output="data")

    runtime = ToolRuntime(_registry(_ReadTool()), ledger=ledger)
    tc = ToolCallComplete("t1", "ReadTool", {})

    await _collect(runtime.invoke(tc))

    ledger2 = ToolLedger(path)
    ledger2.load()
    assert ledger2.committed_by_id == {}
    assert ledger2._replay_pool == {}


# ---- ToolRuntime：重放（防重复副作用） ----------------


@pytest.mark.asyncio
async def test_replay_by_tool_call_id_skips_execution(tmp_path: Path):
    path = tmp_path / "sessions" / "s6.ledger.jsonl"
    # 预置一条已提交的终态（模拟上次会话已执行完）
    ledger = ToolLedger(path)
    ledger.record_outcome(
        "t1", "CounterTool", {"value": 1},
        result={"output": "wrote=1", "is_error": False, "error_code": None, "truncated": False},
        status=SUCCEEDED, elapsed_ms=0.0,
    )
    ledger.load()

    tool = _CounterTool()
    runtime = ToolRuntime(
        _registry(tool), permission_checker=_checker(), ledger=ledger
    )
    tc = ToolCallComplete("t1", "CounterTool", {"value": 1})

    result = await _collect(runtime.invoke(tc))
    # 重放：结果来自账本，工具没被真正执行
    assert result.result.output == "wrote=1"
    assert result.executed is False
    assert tool.calls == []


@pytest.mark.asyncio
async def test_replay_by_fingerprint_skips_execution(tmp_path: Path):
    path = tmp_path / "sessions" / "s7.ledger.jsonl"
    ledger = ToolLedger(path)
    ledger.record_outcome(
        "old-id", "CounterTool", {"value": 5},
        result={"output": "wrote=5", "is_error": False, "error_code": None, "truncated": False},
        status=SUCCEEDED, elapsed_ms=0.0,
    )
    ledger.load()

    tool = _CounterTool()
    runtime = ToolRuntime(
        _registry(tool), permission_checker=_checker(), ledger=ledger
    )
    # 换了新 tool_call_id，但内容指纹一致 → 仍被拦截
    tc = ToolCallComplete("new-id", "CounterTool", {"value": 5})

    result = await _collect(runtime.invoke(tc))
    assert result.result.output == "wrote=5"
    assert result.executed is False
    assert tool.calls == []


@pytest.mark.asyncio
async def test_replay_pool_is_consumed_once(tmp_path: Path):
    path = tmp_path / "sessions" / "s8.ledger.jsonl"
    ledger = ToolLedger(path)
    ledger.record_outcome(
        "old-id", "CounterTool", {"value": 3},
        result={"output": "wrote=3", "is_error": False, "error_code": None, "truncated": False},
        status=SUCCEEDED, elapsed_ms=0.0,
    )
    ledger.load()

    tool = _CounterTool()
    runtime = ToolRuntime(
        _registry(tool), permission_checker=_checker(), ledger=ledger
    )

    # 第一次（等价动作）被重放，第二次真正执行
    r1 = await _collect(runtime.invoke(ToolCallComplete("a", "CounterTool", {"value": 3})))
    r2 = await _collect(runtime.invoke(ToolCallComplete("b", "CounterTool", {"value": 3})))

    assert r1.executed is False
    assert r1.result.output == "wrote=3"
    assert r2.executed is True
    assert tool.calls == [{"value": 3}]


@pytest.mark.asyncio
async def test_denied_entry_is_not_replayed(tmp_path: Path):
    path = tmp_path / "sessions" / "s9.ledger.jsonl"
    ledger = ToolLedger(path)
    ledger.record_outcome(
        "t1", "CounterTool", {"value": 1},
        result={"output": "Permission denied", "is_error": True, "error_code": "permission_denied", "truncated": False},
        status=DENIED, elapsed_ms=0.0,
    )
    ledger.load()

    tool = _CounterTool()
    runtime = ToolRuntime(
        _registry(tool), permission_checker=_checker(), ledger=ledger
    )
    tc = ToolCallComplete("t1", "CounterTool", {"value": 1})

    result = await _collect(runtime.invoke(tc))
    # denied 不被重放：按正常路径执行（BYPASS 下直接跑）
    assert result.executed is True
    assert tool.calls == [{"value": 1}]


# ---- resolve_dangling_tool_uses ----------------


def test_resolve_dangling_tool_uses_fills_recorded_result():
    messages = [
        Message(
            role="assistant",
            content="",
            tool_uses=[ToolUseBlock(tool_use_id="t1", tool_name="CounterTool", arguments={"value": 1})],
        )
    ]

    def resolve(tool_use_id: str):
        if tool_use_id == "t1":
            return ("wrote=1", False)
        return None

    out = resolve_dangling_tool_uses(messages, resolve)
    # 补了一条 tool_result 消息
    assert len(out) == 2
    result_msg = out[1]
    assert result_msg.tool_results[0].tool_use_id == "t1"
    assert REPLAYED_TOOL_RESULT_PREFIX in result_msg.tool_results[0].content
    assert "wrote=1" in result_msg.tool_results[0].content
    assert result_msg.tool_results[0].is_error is False


def test_resolve_dangling_tool_uses_keeps_unresolved():
    messages = [
        Message(
            role="assistant",
            content="",
            tool_uses=[ToolUseBlock(tool_use_id="t1", tool_name="CounterTool", arguments={})],
        )
    ]

    def resolve(tool_use_id: str):
        return None

    out = resolve_dangling_tool_uses(messages, resolve)
    # 没有记录 → 不补，保持悬空（由 ensure_tool_pairing 兜底）
    assert len(out) == 1
    assert out[0].tool_uses[0].tool_use_id == "t1"
