from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kyncode.config import AppConfig, _load_single_file, _merge_config
from kyncode.tools.agent_tool import AgentTool
from kyncode.validator import ConfigError, validate_config_structure


def _minimal_raw(**extra: object) -> dict:
    raw: dict = {
        "providers": [
            {
                "name": "deepseek",
                "protocol": "openai-compat",
                "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            }
        ]
    }
    raw.update(extra)
    return raw


def _provider_config() -> MagicMock:
    cfg = MagicMock()
    cfg.protocol = "openai-compat"
    cfg.base_url = "https://api.deepseek.com"
    cfg.model = "deepseek-v4-flash"
    cfg.api_key = ""
    cfg.context_window = 0
    return cfg


def _make_tool(subagent_model: str = "", provider_config=None, parent_agent=None) -> AgentTool:
    return AgentTool(
        agent_loader=object(),
        task_manager=object(),
        trace_manager=object(),
        parent_agent=parent_agent or MagicMock(),
        provider_config=provider_config,
        subagent_model=subagent_model,
    )


# ---- validator 层 ----

def test_subagent_model_default_empty():
    assert validate_config_structure(_minimal_raw())["subagent_model"] == ""


def test_subagent_model_parsed_and_stripped():
    v = validate_config_structure(_minimal_raw(subagent_model="  deepseek-v4-pro  "))
    assert v["subagent_model"] == "deepseek-v4-pro"


def test_subagent_model_rejects_non_string():
    with pytest.raises(ConfigError):
        validate_config_structure(_minimal_raw(subagent_model=123))


# ---- config 加载层 ----

def test_load_single_file_parses_subagent_model(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text(
        "providers:\n"
        "  - name: deepseek\n"
        "    protocol: openai-compat\n"
        "    base_url: https://api.deepseek.com\n"
        "    model: deepseek-v4-flash\n"
        "subagent_model: deepseek-v4-pro\n",
        encoding="utf-8",
    )
    assert _load_single_file(f).subagent_model == "deepseek-v4-pro"


def test_merge_overrides_only_when_set():
    base = AppConfig(providers=[], subagent_model="deepseek-v4-pro")
    # 空串（未配置）不覆盖底层的值
    _merge_config(base, AppConfig(providers=[], subagent_model=""))
    assert base.subagent_model == "deepseek-v4-pro"
    # 非空才覆盖
    _merge_config(base, AppConfig(providers=[], subagent_model="deepseek-v4-flash"))
    assert base.subagent_model == "deepseek-v4-flash"


# ---- AgentTool 模型解析层 ----

def test_resolve_client_uses_configured_model():
    tool = _make_tool(subagent_model="deepseek-v4-pro", provider_config=_provider_config())
    with patch("kyncode.client.create_client") as mock_create:
        mock_create.return_value = MagicMock()
        client = tool._resolve_client()
        assert client is not None
        assert mock_create.call_args[0][0].model == "deepseek-v4-pro"


def test_resolve_client_reuses_parent_when_unconfigured():
    parent = MagicMock()
    parent.client = MagicMock()
    tool = _make_tool(subagent_model="", provider_config=_provider_config(), parent_agent=parent)
    with patch("kyncode.client.create_client") as mock_create:
        assert tool._resolve_client() is parent.client
        mock_create.assert_not_called()


def test_resolve_client_falls_back_on_create_failure():
    parent = MagicMock()
    parent.client = MagicMock()
    tool = _make_tool(
        subagent_model="deepseek-v4-pro",
        provider_config=_provider_config(),
        parent_agent=parent,
    )
    with patch("kyncode.client.create_client", side_effect=Exception("boom")):
        assert tool._resolve_client() is parent.client
