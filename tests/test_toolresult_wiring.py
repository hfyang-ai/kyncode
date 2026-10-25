"""工具结果预算在 Agent 主循环里的接线测试：驱动完整主循环，
验证单条溢写、聚合溢写、回读豁免，以及进入对话历史的内容就是最终形态。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel
from test_agent import MockLLMClient

from kyncode.agent import Agent
from kyncode.context.manager import AGGREGATE_TOKEN_LIMIT, READBACK_TOKEN_LIMIT
from kyncode.conversation import ConversationManager, estimate_text_tokens
from kyncode.tools import ToolRegistry
from kyncode.tools.base import (
    StreamEnd,
    TextDelta,
    Tool,
    ToolCallComplete,
    ToolResult,
)


class _EmptyParams(BaseModel):
    pass


class _ReadbackParams(BaseModel):
    file_path: str = ""


class FixedOutputTool(Tool):
    """返回固定内容的假工具。"""

    description = "fixed output"
    category = "read"
    params_model = _EmptyParams

    def __init__(self, name: str, output: str) -> None:
        self.name = name
        self._output = output

    async def execute(self, params: BaseModel) -> ToolResult:
        return ToolResult(output=self._output)


class FakeReadFileTool(Tool):
    """名字叫 ReadFile 的假工具，用于触发回读豁免判定。"""

    name = "ReadFile"
    description = "fake readfile"
    category = "read"
    params_model = _ReadbackParams

    def __init__(self, output: str) -> None:
        self._output = output

    async def execute(self, params: BaseModel) -> ToolResult:
        return ToolResult(output=self._output)


def _tool_results_msg(conv: ConversationManager):
    for msg in conv.history:
        if msg.tool_results:
            return msg
    raise AssertionError("no tool-results message in conversation")


def _end_turn() -> list:
    return [TextDelta("done"), StreamEnd("end_turn", input_tokens=1, output_tokens=1)]


@pytest.mark.asyncio
async def test_ingest_spills_single_oversized_result(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    client = MockLLMClient(
        [
            [ToolCallComplete("t1", "BigTool", {}), StreamEnd("tool_use", 1, 1)],
            _end_turn(),
        ]
    )
    registry = ToolRegistry()
    # 20K 中文字符 ≈ 20K token，超过单条 15K token 阈值
    registry.register(FixedOutputTool("BigTool", "中" * 20_000))
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("go")

    async for _ in agent.run(conv):
        pass

    # 进历史的内容是预览，不是原文
    tr = _tool_results_msg(conv).tool_results[0]
    assert tr.content.startswith("<persisted-output>")
    # 溢写文件保存了完整原文
    spill = agent.session_dir / "t1.txt"
    assert spill.exists()
    assert len(spill.read_text(encoding="utf-8")) == 20_000


@pytest.mark.asyncio
async def test_ingest_readback_exempt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    registry = ToolRegistry()
    registry.register(FakeReadFileTool("中" * 20_000))
    # 先构造 agent 拿到 session_dir，再让 ReadFile 指向该目录下的文件
    client_placeholder = MockLLMClient([])
    probe = Agent(client_placeholder, registry, "anthropic", work_dir=str(tmp_path))
    readback_path = str(probe.session_dir / "toolu_old.txt")

    client = MockLLMClient(
        [
            [
                ToolCallComplete("t_rb", "ReadFile", {"file_path": readback_path}),
                StreamEnd("tool_use", 1, 1),
            ],
            _end_turn(),
        ]
    )
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("read it back")

    async for _ in agent.run(conv):
        pass

    # 回读结果豁免溢写：原文进历史，且没有生成新的溢写文件
    tr = _tool_results_msg(conv).tool_results[0]
    assert len(tr.content) == 20_000
    assert not (agent.session_dir / "t_rb.txt").exists()


@pytest.mark.asyncio
async def test_ingest_readback_truncates_oversized(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    registry = ToolRegistry()
    # 读回 35K token，超过 READBACK_TOKEN_LIMIT (30K)：豁免落盘但需截断，
    # 防止一次读回超大文件把上下文窗口撑爆
    registry.register(FakeReadFileTool("中" * 35_000))
    client_placeholder = MockLLMClient([])
    probe = Agent(client_placeholder, registry, "anthropic", work_dir=str(tmp_path))
    readback_path = str(probe.session_dir / "toolu_old.txt")

    client = MockLLMClient(
        [
            [
                ToolCallComplete("t_rb", "ReadFile", {"file_path": readback_path}),
                StreamEnd("tool_use", 1, 1),
            ],
            _end_turn(),
        ]
    )
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("read it back")

    async for _ in agent.run(conv):
        pass

    tr = _tool_results_msg(conv).tool_results[0]
    # 进历史的是「截断到上限的前缀 + 提示」，而非 35K 原文
    assert tr.content.startswith("中" * READBACK_TOKEN_LIMIT)
    assert "已截断" in tr.content and "分块读取" in tr.content
    assert len(tr.content) < 35_000
    # 截断而非落盘：不生成溢写文件
    assert not (agent.session_dir / "t_rb.txt").exists()


@pytest.mark.asyncio
async def test_ingest_aggregate_spills_largest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # 中文字符 ≈ token：5 条合计约 62K token，溢写最大的 t3 后回到 60K 内
    sizes = {"T1": 12_000, "T2": 12_000, "T3": 14_000, "T4": 12_000, "T5": 12_000}
    registry = ToolRegistry()
    calls = []
    for name, n in sizes.items():
        registry.register(FixedOutputTool(name, "中" * n))
        calls.append(ToolCallComplete("t" + name[1:].lower(), name, {}))
    client = MockLLMClient(
        [
            [*calls, StreamEnd("tool_use", 1, 1)],
            _end_turn(),
        ]
    )
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("fan out")

    async for _ in agent.run(conv):
        pass

    msg = _tool_results_msg(conv)
    total = sum(estimate_text_tokens(tr.content) for tr in msg.tool_results)
    assert total <= AGGREGATE_TOKEN_LIMIT
    previews = [
        tr for tr in msg.tool_results if tr.content.startswith("<persisted-output>")
    ]
    assert len(previews) == 1
    t3 = next(tr for tr in msg.tool_results if tr.tool_use_id == "t3")
    assert t3.content.startswith("<persisted-output>")
