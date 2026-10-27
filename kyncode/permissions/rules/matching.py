from __future__ import annotations

from kyncode.permissions.rules.model import Effect, Rule

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
