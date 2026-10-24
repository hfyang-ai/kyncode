from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolate_teams_dir(tmp_path):
    """把团队根目录指到临时目录，让团队配置落在测试自己的沙箱里。

    团队目录是 <home>/.kyncode/teams，不重定向的话跑一次测试就会在真实主目录里
    留下一堆 squad、squad-2 这样的残留，下一次跑还会撞上「团队已存在」。

    只替换 teams.models 里的 Path.home，不动全局：memory 等模块同样按主目录
    解析路径，全局替换会连带改掉它们的行为。目录名避开 "home"，
    memory 的用例会在同一个 tmp_path 下自己建这个名字的目录。
    """
    home = tmp_path / "teams-home"
    home.mkdir(parents=True, exist_ok=True)
    with patch("kyncode.teams.models.Path.home", return_value=home):
        yield


@pytest.fixture(autouse=True)
def isolate_trace_dir(tmp_path):
    """把追踪落盘目录指到临时目录，避免测试把 trace JSON 写进真实 ~/.kyncode/trace。

    DEFAULT_TRACE_DIR 在 import 时已按 Path.home() 求值，patch Path.home 无效，
    必须直接替换模块常量。TraceManager() 不传 state_dir 时走默认构造，落到这里。
    """
    with patch(
        "kyncode.agents.trace.DEFAULT_TRACE_DIR",
        tmp_path / "trace",
    ):
        yield
