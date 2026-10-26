"""Agent Adapter：统一待测 Agent 的调用接口，与 Grader / Orchestrator 解耦。

长文方案核心设计之一：评测器不应直接依赖 Agent 内部的类、函数和私有状态。
这里只定义进程级契约——输入一个 ``AgentRequest``（任务 + 工作区 + 超时），
输出一个 ``AgentResult``（进程执行事实），评分由独立的 Grader 产生。

未来可以替换为其他 Agent（不同 CLI / 不同模型 / 远程 Agent），而无需改动
Orchestrator 与 Grader。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """一次 Agent 执行的输入。"""

    task_id: str
    prompt: str
    workspace: Path
    timeout_seconds: int


@dataclass
class AgentResult:
    """Agent 进程执行结果（事实记录，不含评分）。"""

    exit_code: int | None
    timed_out: bool
    duration_seconds: float
    stdout: str
    stderr: str
    final_text: str
    num_turns: int
    tool_calls: list[dict[str, Any]]
    errors: list[str]
    stop_reason: str
    input_tokens: int
    output_tokens: int
    raw_ndjson: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class AgentAdapter(Protocol):
    async def run(self, request: AgentRequest) -> AgentResult:
        """执行一次 Agent，返回进程级结果。"""
        ...


class CLIAgentAdapter:
    """通过 ``asyncio.create_subprocess_exec`` 调用本地 CLI agent。

    安全要点（长文方案）：
    - 不拼接 shell 字符串，参数用列表直接传 exec。
    - stdout / stderr 分开保存，避免无限制读入内存。
    - 超时后终止整个进程组，清理子进程。
    """

    def __init__(self, agent_bin: str, agent_config: str = "") -> None:
        self.agent_bin = agent_bin
        self.agent_config = agent_config

    async def run(self, request: AgentRequest) -> AgentResult:
        cmd = [self.agent_bin, "-p", request.prompt, "--output-format", "stream-json"]
        # 待测 agent 在隔离 workspace 里找不到自身 config 时，注入一份精简 config：
        # 复用 agent_config 里的 providers，去掉 mcp_servers（eval 时 npx 启动 MCP 会卡住）。
        self._inject_agent_config(request.workspace)
        start = time.monotonic()
        timed_out = False
        returncode: int | None = None
        stdout = ""
        stderr = ""

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(request.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # 独立会话，便于超时后 killpg 整个进程组
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=request.timeout_seconds
                )
                returncode = proc.returncode
                stdout = stdout_b.decode("utf-8", errors="ignore")
                stderr = stderr_b.decode("utf-8", errors="ignore")
            except asyncio.TimeoutError:
                timed_out = True
                returncode = proc.returncode
                _kill_process_group(proc.pid)
        except OSError as e:
            stderr = str(e)
            returncode = -1

        duration = time.monotonic() - start
        parsed = _parse_ndjson(stdout)
        return AgentResult(
            exit_code=returncode,
            timed_out=timed_out,
            duration_seconds=round(duration, 2),
            stdout=stdout,
            stderr=stderr,
            final_text=parsed["result"],
            num_turns=parsed["num_turns"],
            tool_calls=parsed["tool_calls"],
            errors=parsed["errors"],
            stop_reason=parsed["stop_reason"],
            input_tokens=parsed["usage"]["input_tokens"],
            output_tokens=parsed["usage"]["output_tokens"],
            raw_ndjson=[ln for ln in stdout.splitlines() if ln.strip()],
        )

    def _inject_agent_config(self, workspace: Path) -> None:
        """把精简的 agent config 注入到 workspace，让 agent 能找到 provider 配置。

        复用 agent_config 的 providers，mcp_servers 置空（eval 场景下 npx 启动
        MCP 会卡住或拉长启动时间）。题目 setup 可自带 config.yaml 声明评测约束
        （hooks / permission_mode），但不带 providers（API key 不进题面）——这里
        合并：providers 始终来自 agent_config，hooks / permission_mode 优先取题目
        自带的覆盖，其余字段回退到 agent_config。
        """
        if not self.agent_config:
            return
        src = Path(self.agent_config)
        if not src.exists():
            return
        try:
            raw = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return
        slim = {
            "providers": raw.get("providers", []),
            "permission_mode": raw.get("permission_mode", "default"),
            "mcp_servers": [],
        }
        dst = workspace / "config.yaml"
        task_cfg: dict[str, Any] = {}
        if dst.exists():
            try:
                task_cfg = yaml.safe_load(dst.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                task_cfg = {}
        if task_cfg.get("hooks"):
            slim["hooks"] = task_cfg["hooks"]
        if task_cfg.get("permission_mode"):
            slim["permission_mode"] = task_cfg["permission_mode"]
        dst.write_text(
            yaml.safe_dump(slim, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


async def _kill_process_group(pid: int | None) -> None:
    """先 SIGTERM 整个进程组，超时再 SIGKILL。"""
    if pid is None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        try:
            await asyncio.wait_for(_wait_pid(pid), timeout=5)
            return
        except asyncio.TimeoutError:
            continue


async def _wait_pid(pid: int) -> None:
    # 进程组已 killpg，这里仅做一个短暂的让步，避免忙等
    await asyncio.sleep(0.1)


def _parse_ndjson(stdout: str) -> dict[str, Any]:
    """从 stream-json 输出解析 result / usage / tool_calls / errors。"""
    usage = {"input_tokens": 0, "output_tokens": 0}
    result_text = ""
    num_turns = 0
    tool_calls: list[dict[str, Any]] = []
    errors: list[str] = []
    stop_reason = ""

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        if etype == "usage":
            usage = {
                "input_tokens": int(evt.get("input_tokens", 0)),
                "output_tokens": int(evt.get("output_tokens", 0)),
            }
        elif etype == "result":
            result_text = evt.get("result", "")
            num_turns = int(evt.get("num_turns", 0))
            tool_calls = evt.get("tool_calls", [])
            usage = {
                "input_tokens": int(evt.get("usage", {}).get("input_tokens", 0)),
                "output_tokens": int(evt.get("usage", {}).get("output_tokens", 0)),
            }
            stop_reason = evt.get("stop_reason", "")
        elif etype == "error":
            errors.append(evt.get("message", "unknown error"))
        elif etype == "tool_use":
            tool_calls.append(
                {"name": evt.get("tool_name", ""), "is_error": False}
            )

    return {
        "usage": usage,
        "result": result_text,
        "num_turns": num_turns,
        "tool_calls": tool_calls,
        "errors": errors,
        "stop_reason": stop_reason,
    }


def _truncate_text(text: str, max_chars: int) -> str:
    """截断长文本，保留头尾，超长加省略标记。"""
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars // 2
    return text[:head] + f"\n...[truncated {len(text) - max_chars} chars]...\n" + text[-tail:]


def parse_trace(stdout: str) -> list[dict[str, Any]]:
    """从 stream-json 提取精炼的工具调用轨迹，供 LLM 失败分析使用。

    每个 turn：``{"turn", "tool", "args", "output", "is_error", "elapsed"}``。
    ``tool_use``（含 args）与 ``tool_result``（含 output/is_error/elapsed）按
    ``tool_id`` 配对；错误结果保留更长输出（2000 字符）以便定位根因。
    """
    trace: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    turn = 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = evt.get("type")
        if etype == "tool_use":
            tid = str(evt.get("tool_id") or turn)
            args = evt.get("args")
            pending[tid] = {
                "turn": turn,
                "tool": evt.get("tool_name", ""),
                "args": _truncate_text(json.dumps(args, ensure_ascii=False), 500)
                if args is not None
                else "",
                "is_error": False,
                "output": "",
                "elapsed": 0.0,
            }
            turn += 1
        elif etype == "tool_result":
            tid = str(evt.get("tool_id") or "")
            entry = pending.pop(tid, None) if tid else None
            if entry is None and pending:
                # tool_id 缺失/不匹配时，配对最近一个未完成的工具调用
                entry = pending.pop(next(reversed(pending)))
            if entry is None:
                continue
            is_error = bool(evt.get("is_error", False))
            entry["output"] = _truncate_text(
                str(evt.get("output", "")), 2000 if is_error else 500
            )
            entry["is_error"] = is_error
            entry["elapsed"] = evt.get("elapsed", 0.0)
            trace.append(entry)
    return trace
