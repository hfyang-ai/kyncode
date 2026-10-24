from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import BaseModel

from kyncode.agent import Agent
from kyncode.agents.trace import TraceManager
from kyncode.commands.handlers.trace import create_trace_command
from kyncode.tools import ToolRegistry
from kyncode.tools.base import Tool, ToolCallComplete, ToolResult
from kyncode.tracing import current_agent_id


def test_register_lead_creates_root(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    node = tm.register_lead("lead123456789012")

    assert node.agent_id == "lead123456789012"
    assert node.parent_id is None
    assert node.trace_id == "lead123456789012"
    assert node.agent_type == "lead"
    assert tm.get("lead123456789012") is node


def test_create_inherits_trace_id_from_parent(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    lead = tm.register_lead("lead123456789012")
    child = tm.create(
        agent_type="explore", parent_id=lead.agent_id, trace_id=lead.trace_id
    )

    assert child.parent_id == lead.agent_id
    assert child.trace_id == lead.trace_id

    tree = tm.get_tree(lead.trace_id)
    assert len(tree) == 2
    assert {n.agent_id for n in tree} == {lead.agent_id, child.agent_id}


def test_persist_writes_json_per_trace(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    lead = tm.register_lead("lead123456789012")
    child = tm.create("explore", parent_id=lead.agent_id, trace_id=lead.trace_id)
    tm.update(child.agent_id, input_tokens=100, output_tokens=50)
    tm.complete(child.agent_id, "completed")

    path = tmp_path / "lead123456789012.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["traceId"] == lead.trace_id
    assert len(data["nodes"]) == 2

    child_data = next(n for n in data["nodes"] if n["agentId"] == child.agent_id)
    assert child_data["status"] == "completed"
    assert child_data["inputTokens"] == 100
    assert child_data["outputTokens"] == 50


def test_load_and_list_traces(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    lead = tm.register_lead("lead123456789012")
    tm.create("explore", parent_id=lead.agent_id, trace_id=lead.trace_id)

    loaded = tm.load(lead.trace_id)
    assert loaded is not None
    assert len(loaded) == 2
    assert {n.agent_type for n in loaded} == {"lead", "explore"}

    summaries = tm.list_traces()
    assert len(summaries) == 1
    assert summaries[0]["trace_id"] == lead.trace_id
    assert summaries[0]["node_count"] == 2
    assert summaries[0]["total_in"] == 0


def test_load_missing_trace_returns_none(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    assert tm.load("nonexistent") is None
    assert tm.list_traces() == []


def test_remove_drops_node_from_persisted_file(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    lead = tm.register_lead("lead123456789012")
    child = tm.create("explore", parent_id=lead.agent_id, trace_id=lead.trace_id)

    tm.remove(child.agent_id)
    assert tm.get(child.agent_id) is None

    data = json.loads((tmp_path / "lead123456789012.json").read_text(encoding="utf-8"))
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["agentId"] == lead.agent_id


def _make_ctx(args: str = "") -> SimpleNamespace:
    return SimpleNamespace(args=args, ui=MagicMock())


async def test_trace_command_renders_tree_with_lead_root(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    tm.register_lead("lead123456789012")
    tm.create("explore", parent_id="lead123456789012", trace_id="lead123456789012")
    cmd = create_trace_command(tm)

    ctx = _make_ctx("")
    await cmd.handler(ctx)
    text = ctx.ui.add_system_message.call_args[0][0]
    assert "lead" in text
    assert "explore" in text
    assert "合计: 2 agents" in text


async def test_trace_command_list(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    tm.register_lead("lead123456789012")
    cmd = create_trace_command(tm)

    ctx = _make_ctx("list")
    await cmd.handler(ctx)
    text = ctx.ui.add_system_message.call_args[0][0]
    assert "lead1234" in text
    assert "1 agents" in text


async def test_trace_command_load_history_by_id(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    tm.register_lead("lead123456789012")
    tm.create("explore", parent_id="lead123456789012", trace_id="lead123456789012")
    cmd = create_trace_command(tm)

    ctx = _make_ctx("lead123456789012")
    await cmd.handler(ctx)
    text = ctx.ui.add_system_message.call_args[0][0]
    assert "lead" in text
    assert "explore" in text


async def test_trace_command_unknown_id(tmp_path):
    tm = TraceManager(state_dir=tmp_path)
    tm.register_lead("lead123456789012")
    cmd = create_trace_command(tm)

    ctx = _make_ctx("does-not-exist")
    await cmd.handler(ctx)
    text = ctx.ui.add_system_message.call_args[0][0]
    assert "未找到" in text


# =====================================================================
# 子 agent 工具调用追踪：agent_id 归属 + tool_call_count 累加
# =====================================================================


async def test_run_to_completion_sets_current_agent_id():
    """run_to_completion 应像 run() 一样绑定 current_agent_id 到本 agent，
    结束时 reset 回原值，让子 agent 的工具调用日志归属正确。"""
    agent = Agent(MagicMock(), ToolRegistry(), "anthropic", work_dir=".")
    seen: dict[str, str | None] = {}

    async def fake_loop(*args, **kwargs):
        seen["agent_id"] = current_agent_id.get()
        return "done"

    with patch("kyncode.agent.run_agent_loop_to_completion", new=fake_loop):
        result = await agent.run_to_completion("hi")

    assert result == "done"
    assert seen["agent_id"] == agent.agent_id
    # finally 里已 reset，回到进入前的值（None）
    assert current_agent_id.get() is None


class _EmptyParams(BaseModel):
    pass


class EchoTool(Tool):
    params_model = _EmptyParams

    def __init__(self) -> None:
        self.name = "Echo"
        self.description = "Echo back"
        self.category = "read"

    def get_schema(self):
        return {"name": self.name, "description": self.description, "input_schema": {}}

    async def execute(self, params):
        return ToolResult(output="ok")


async def test_tool_call_count_accumulates_on_execution():
    """真正执行的工具调用会累加 agent.tool_call_count。"""
    reg = ToolRegistry()
    reg.register(EchoTool())
    agent = Agent(MagicMock(), reg, "anthropic", work_dir=".")
    assert agent.tool_call_count == 0

    result = await agent.execute_tool_noninteractive(
        ToolCallComplete("t1", "Echo", {})
    )
    assert result.output == "ok"
    assert agent.tool_call_count == 1


async def test_tool_call_count_not_incremented_on_short_circuit():
    """短路（未知工具）不应累加 tool_call_count——只有 executed 才算。"""
    reg = ToolRegistry()
    agent = Agent(MagicMock(), reg, "anthropic", work_dir=".")

    result = await agent.execute_tool_noninteractive(
        ToolCallComplete("t1", "NoSuchTool", {})
    )
    assert result.is_error is True
    assert agent.tool_call_count == 0
