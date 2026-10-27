from __future__ import annotations

import re
from typing import Any

from kyncode.permissions.rules.model import Effect, Rule

_RULE_RE = re.compile(r"^(\w+)\((.+)\)$")

_CONTENT_FIELDS: dict[str, str] = {
    "Bash": "command",
    "ReadFile": "file_path",
    "WriteFile": "file_path",
    "EditFile": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "WebFetch": "url",
    "WebSearch": "query",
}

# content 字段是「文件路径」的工具。对这类工具，「允许并不再询问」的授权
# 会按工作区泛化：处理工作区内文件不再询问，只有工作区外文件才询问
# （见 ToolRuntime._build_allow_pattern）。
FILE_PATH_TOOLS = frozenset(
    name for name, field in _CONTENT_FIELDS.items() if field == "file_path"
)

# 匹配命令开头的 `cd <path> && ` / `cd <path>; ` 前缀（可多层）。
# 路径可能是裸路径、单/双引号包裹的路径，或 - / ~ / .. 这类 cd 特殊参数。
# 只有 cd 后面跟了 && 或 ;（即后面还有实际动作）才算「可剥的前缀」——
# 纯 `cd <path>` 后面没有动作，不匹配，原样保留。
_CD_PREFIX_RE = re.compile(
    r'^\s*cd\s+(?:"[^"]*"|\'[^\']*\'|[^\s;&|]+)\s*(?:&&|;)\s*'
)


def strip_cd_prefix(command: str) -> str:
    """剥掉命令开头的 ``cd <path> && `` / ``cd <path>; `` 前缀，支持多层。

    cd 在每次 Bash 调用里都跑在独立子进程中，只改变该子进程自己的工作目录，
    不影响宿主进程、也不影响下一次调用，是零副作用的局部动作。它不该进入
    授权匹配对象——否则 LLM 习惯性写 ``cd /path && cmd`` 时，同一条实际动作
    会因 cd 前缀不同而反复触发询问。剥掉后授权聚焦到 cd 之后真正执行的命令。
    """
    while True:
        stripped = _CD_PREFIX_RE.sub("", command, count=1)
        if stripped == command:
            return stripped
        if not stripped:
            # 剥完变空（如 "cd /tmp && " 这类悬空命令），保留剥之前的状态，
            # 避免空 content 在落盘时退化成 "*" 而放行一切。
            return command
        command = stripped


def parse_rule(raw: str, effect: Effect) -> Rule:
    m = _RULE_RE.match(raw.strip())
    if not m:
        raise ValueError(f"无效的规则语法: {raw}")
    return Rule(tool_name=m.group(1), pattern=m.group(2), effect=effect)


def extract_content(tool_name: str, arguments: dict[str, Any]) -> str:
    # mcp_call 的匹配对象不是某一个参数，而是「要调用哪个 MCP 工具」，
    # 由 server + tool 两个参数合成 server__tool。这样规则写成
    # mcp_call(linear__*) 就能按服务器或按工具做 allow/deny。
    if tool_name == "mcp_call":
        from kyncode.tools.mcp_call import permission_content

        return permission_content(
            str(arguments.get("server", "")), str(arguments.get("tool", ""))
        )
    field = _CONTENT_FIELDS.get(tool_name)
    if field is None:
        return ""
    content = str(arguments.get(field, ""))
    if tool_name == "Bash":
        return strip_cd_prefix(content)
    return content
