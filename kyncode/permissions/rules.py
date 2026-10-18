from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

import yaml

log = logging.getLogger(__name__)

Effect = Literal["allow", "deny", "ask"]

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


@dataclass(frozen=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect

    def matches(self, tool_name: str, content: str) -> bool:
        if self.tool_name != tool_name:
            return False
        return fnmatch(content, self.pattern)


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


_GLOB_META = ("*", "?", "[", "]")


def _suffix_wildcard_prefix(pattern: str) -> str | None:
    """pattern 若是「字面量 + 末尾通配」形式（literal*），返回 literal。

    literal 部分不含任何 glob 元字符（* ? [ ]）时，才能用前缀包含做精确覆盖
    判定；含元字符时返回 None，调用方应保守地不做覆盖判定。
    """
    if not pattern.endswith("*"):
        return None
    literal = pattern[:-1]
    if any(ch in literal for ch in _GLOB_META):
        return None
    return literal


def _pattern_covers(wide: str, narrow: str) -> bool:
    """wide 是否覆盖 narrow —— 任何 narrow 能匹配的字符串 wide 也能匹配。

    只对「字面量 + 末尾 *」形式的 pattern 做精确判定：wide = la*、narrow = lb*，
    当 la 是 lb 的前缀时 wide 覆盖 narrow。其他更复杂的 glob（含 ?/[ ]/中间 *）
    保守返回 False，避免误删。
    """
    w = _suffix_wildcard_prefix(wide)
    n = _suffix_wildcard_prefix(narrow)
    if w is None or n is None:
        return False
    return n.startswith(w)


def _redundancy(rule: Rule, existing: list[Rule]) -> str | None:
    """判断 rule 相对 existing 是否冗余，返回原因；不冗余返回 None。

    - "duplicate"：与已有规则完全相同（tool_name + pattern + effect）
    - "superset"：被已有同 effect 规则覆盖（更宽规则已放行/拒绝同一动作面）
    """
    for e in existing:
        if e.tool_name != rule.tool_name or e.effect != rule.effect:
            continue
        if e.pattern == rule.pattern:
            return "duplicate"
        if _pattern_covers(e.pattern, rule.pattern):
            return "superset"
    return None


def _load_rules_file(path: Path) -> list[Rule]:
    if not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    rules: list[Rule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rule_str = entry.get("rule", "")
        effect = entry.get("effect", "")
        if effect not in ("allow", "deny", "ask"):
            continue
        try:
            rules.append(parse_rule(rule_str, effect))
        except ValueError:
            continue
    return rules


class RuleEngine:
    def __init__(
        self,
        user_rules_path: Path | None = None,
        project_rules_path: Path | None = None,
        local_rules_path: Path | None = None,
    ) -> None:
        self._user_path = user_rules_path
        self._project_path = project_rules_path
        self._local_path = local_rules_path
        # 规则文件解析结果缓存：path -> ((mtime_ns, size), rules)
        self._cache: dict[Path, tuple[tuple[int, int], list[Rule]]] = {}

    def _rules_for(self, path: Path | None) -> list[Rule]:
        """读取单个规则文件，命中缓存时不读盘也不解析。

        以 mtime + size 作为文件是否变动的依据，只比 mtime 不够：
        同一秒内的连续改写在部分文件系统上时间戳可能不变。
        """
        if path is None:
            return []

        try:
            st = path.stat()
        except OSError:
            # 文件不存在或读不到，按空规则处理，同时清掉可能存在的旧缓存
            self._cache.pop(path, None)
            return []

        stamp = (st.st_mtime_ns, st.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]

        rules = _load_rules_file(path)
        self._cache[path] = (stamp, rules)
        return rules

    def snapshot(self) -> list[Rule]:
        """取三份规则文件的合并快照。

        文件没变动时直接复用上次的解析结果，变动了才重新读盘，
        因此改完规则文件下次评估即刻生效，反复评估也不会重复解析。
        一次工具调用取一次快照，复合命令逐条检查子命令时共用它。
        """
        rules: list[Rule] = []
        for p in (self._user_path, self._project_path, self._local_path):
            rules.extend(self._rules_for(p))
        return rules

    def evaluate(self, tool_name: str, content: str) -> Effect | None:
        """把三份规则文件的规则合并成一个集合，返回命中规则中最严格的效果。

        优先级 deny > ask > allow：规则写在哪一层、写在文件第几行都不影响裁决，
        因此一条 deny 无法被其他层的 allow 抵消。没有任何规则命中时返回 None。
        """
        return evaluate_rules(self.snapshot(), tool_name, content)

    def append_local_rule(self, rule: Rule) -> None:
        if self._local_path is None:
            return
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_rules_file(self._local_path)

        reason = _redundancy(rule, existing)
        if reason is not None:
            # 去重：相同规则不重复写，被已有更宽规则覆盖的也跳过。
            # 审计日志记下这次授权动作被归并，落盘文件保持不变。
            log.info(
                "local_rule_skip %s",
                json.dumps(
                    {
                        "tool": rule.tool_name,
                        "pattern": rule.pattern,
                        "effect": rule.effect,
                        "reason": reason,
                    },
                    ensure_ascii=False,
                ),
            )
            return

        existing.append(rule)
        entries = [
            {"rule": f"{r.tool_name}({r.pattern})", "effect": r.effect}
            for r in existing
        ]
        self._local_path.write_text(
            yaml.dump(entries, allow_unicode=True), encoding="utf-8"
        )
        log.info(
            "local_rule_add %s",
            json.dumps(
                {
                    "tool": rule.tool_name,
                    "pattern": rule.pattern,
                    "effect": rule.effect,
                    "total": len(existing),
                },
                ensure_ascii=False,
            ),
        )


def evaluate_rules(rules: list[Rule], tool_name: str, content: str) -> Effect | None:
    """在给定规则集上裁决，优先级 deny > ask > allow。

    没有任何规则命中时返回 None。
    """
    hit: Effect | None = None
    for rule in rules:
        if not rule.matches(tool_name, content):
            continue
        if rule.effect == "deny":
            # deny 已是最严效果，不可能再被压过，直接返回
            return "deny"
        if rule.effect == "ask":
            hit = "ask"
        elif hit is None:
            # allow 最弱，只在还没命中更严的效果时记录
            hit = "allow"
    return hit
