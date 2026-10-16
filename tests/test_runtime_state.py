from __future__ import annotations

from pathlib import Path

from test_agent import MockLLMClient

from kyncode.agent import Agent
from kyncode.tools import create_default_registry


def _make_agent(tmp_path: Path) -> Agent:
    registry = create_default_registry(web_access=False)
    client = MockLLMClient([])
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    agent.session_id = "session_test"
    return agent


def test_save_and_load_runtime_state(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)
    agent.active_skills["planner"] = "SOP body"
    agent.recovery_state.record_file_read("/tmp/a.py", "print('hi')")
    fsc = agent._file_state_cache()
    assert fsc is not None
    fsc.record("/tmp/a.py", 12345)

    agent.save_runtime_state()
    assert agent.runtime_state_path().exists()

    # 模拟新进程：重建一个 agent，从磁盘恢复运行时心智。
    agent2 = _make_agent(tmp_path)
    agent2.session_id = "session_test"
    agent2.load_runtime_state()

    assert agent2.active_skills == {"planner": "SOP body"}
    assert agent2.recovery_state._files["/tmp/a.py"].content == "print('hi')"
    fsc2 = agent2._file_state_cache()
    assert fsc2 is not None
    assert fsc2._cache.get("/tmp/a.py") == 12345


def test_load_missing_state_is_noop(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)
    agent.session_id = "nonexistent"
    agent.load_runtime_state()  # 不应抛异常
    assert agent.active_skills == {}


def test_build_resume_notice(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)
    agent.active_skills["planner"] = "SOP body"
    fsc = agent._file_state_cache()
    f = tmp_path / "a.txt"
    f.write_text("hello")
    fsc.record(str(f), f.stat().st_mtime_ns)

    notice = agent.build_resume_notice()

    assert "文件状态恢复" in notice
    assert str(f) in notice
    assert "无需重复读取" in notice
    assert "已激活的技能" in notice
    assert "planner" in notice


def test_build_resume_notice_empty_when_nothing(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)
    assert agent.build_resume_notice() == ""
