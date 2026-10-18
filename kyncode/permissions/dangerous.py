from __future__ import annotations

import re

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # ── 文件删除 ──
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/\s*$"), "递归强制删除根目录"),
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+~(?:\s|$)"), "递归强制删除家目录"),
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+\.(?:\s|$)"), "递归强制删除当前目录"),
    (
        re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+\*(?:\s|$)"),
        "递归强制删除通配全部文件",
    ),
    (
        re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+--no-preserve-root\s+/"),
        "绕过安全保护强制删除根目录",
    ),
    # ── 磁盘操作 ──
    (re.compile(r"mkfs\."), "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/"), "直接写磁盘设备"),
    (re.compile(r">\s*/dev/(sd|nvme|disk|mmcblk)"), "覆盖磁盘设备"),
    # ── 权限变更 ──
    (re.compile(r"chmod\s+-R\s+777\s+/"), "递归修改根目录权限为 777"),
    (re.compile(r"chown\s+-R\s+\S+\s+/(?:\s|$)"), "递归修改根目录所有权"),
    # ── 系统关机 ──
    (re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+[06])\b"), "系统关机或重启"),
    (re.compile(r"systemctl\s+(poweroff|reboot|halt)\b"), "systemctl 关机或重启"),
    # ── 网络 / 注入 ──
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh"), "管道执行远程脚本(curl)"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh"), "管道执行远程脚本(wget)"),
    # ── 破坏性 git 操作 ──
    (
        re.compile(r"git\s+push\s+.*--force(?!-with-lease)"),
        "强制推送（非 --force-with-lease）",
    ),
    (re.compile(r"git\s+reset\s+--hard\b"), "硬重置（丢弃所有未提交更改）"),
    (re.compile(r"git\s+clean\s+-[a-z]*f[a-z]*d[a-z]*x[a-z]*\b"), "强力清理未跟踪文件"),
    # ── 覆盖关键系统文件 ──
    (re.compile(r">\s*/etc/(passwd|shadow|sudoers|group)\b"), "覆盖关键系统文件"),
    (re.compile(r"cp\s+/dev/null\s+/etc/(passwd|shadow|sudoers)"), "清空关键系统文件"),
    # ── Docker 破坏 ──
    (
        re.compile(r"docker\s+(rm|container\s+rm)\s+-f\s+.*\$"),
        "强制删除命令替换结果中的所有容器",
    ),
    (
        re.compile(r"docker\s+system\s+prune\s+-[a-z]*f"),
        "强制清理所有未使用的 Docker 资源",
    ),
    # ── 防火墙 ──
    (re.compile(r"iptables\s+-F\b"), "清除所有 iptables 防火墙规则"),
    # ── 删除家目录的变量 / 长选项变体 ──
    (
        re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+\$HOME(?:\s|$)"),
        "递归强制删除家目录($HOME)",
    ),
    (
        re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+\$\{HOME\}(?:\s|$)"),
        "递归强制删除家目录(${HOME})",
    ),
    (re.compile(r"rm\s+.*--recursive[^\s]*\s+--force\b"), "长选项递归强制删除"),
    # ── find 执行 / 批量删除 ──
    (
        re.compile(r"find\s+.*-(?:exec|execdir)\s+(?:rm|sh|bash)\b"),
        "find 执行删除或脚本",
    ),
    (re.compile(r"find\s+.*-delete\b"), "find 批量删除文件"),
    # ── 编码 / 任意命令管道执行 ──
    (re.compile(r"base64\s+.*-d[^\s]*\s*\|\s*(?:ba|z)?sh\b"), "base64 解码后管道执行"),
    (re.compile(r"\|\s*(?:ba|z)?sh\b"), "管道执行 shell 脚本"),
]


_SAFE_COMMANDS = frozenset(
    {
        "ls",
        "dir",
        "pwd",
        "echo",
        "cat",
        "head",
        "tail",
        "wc",
        "find",
        "which",
        "whereis",
        "whoami",
        "hostname",
        "uname",
        "date",
        "cal",
        "uptime",
        "df",
        "du",
        "free",
        "env",
        "printenv",
        "file",
        "stat",
        "readlink",
        "realpath",
        "basename",
        "dirname",
        "sort",
        "uniq",
        "tr",
        "cut",
        "awk",
        "sed",
        "grep",
        "egrep",
        "fgrep",
        "diff",
        "comm",
        "true",
        "false",
        "test",
        "git status",
        "git log",
        "git diff",
        "git show",
        "git branch",
        "git tag",
        "git remote",
        "git rev-parse",
        "git ls-files",
        "git blame",
        "git stash list",
        "go version",
        "go env",
        "node -v",
        "npm -v",
        "python --version",
        "pip list",
        "cargo --version",
        "rustc --version",
        "java -version",
        "java --version",
    }
)


def _has_execution_semantics(command: str) -> bool:
    """白名单命令自身具备执行 / 写文件能力时，检测参数里藏的执行语义。

    awk 的 system() 会直接调 shell；find 的 -exec/-execdir/-delete/-ok 会执行或
    删除文件；sed 的 -i 会就地写文件。这些即使命令名在安全列表里也应视为危险，
    否则「只读白名单」会被当成任意代码执行的入口。
    """
    return (
        (command.startswith("awk") and "system(" in command)
        or (
            command.startswith("find")
            and re.search(r"-(?:exec|execdir|delete|ok)\b", command)
        )
        or (
            command.startswith("sed")
            and re.search(r"(?:^|\s)-i(?:\.\w+)?(?:\s|$)", command)
        )
    )


# 命令替换 / 重定向：这些会写文件或注入执行，任何位置出现都拒绝，不参与拆分。
# 注意这里刻意不列 <（输入重定向）：`cat < f` 这类只读命令历史上一直接放行，
# 而 `<` 后面的命令名会由 _is_safe_single_command 决定（`sh < script` 因 sh 不在
# 白名单而被拒）。
_UNSPLITTABLE_OPERATORS = (">", "$(", "`")


def _split_compound(command: str) -> list[str]:
    """按顶层（非引号内、非转义）的 && / || / ; / | / & 拆分子命令。

    引号与反斜杠转义内的操作符不拆——`grep "a|b"` 的 `|` 是正则、`sed '1,10p;20p'`
    的 `;` 是地址分隔，都不是 shell 操作符。解析偏保守：遇到未闭合引号时把剩余
    内容当作一整段，宁可少拆也不误拆，避免把一个含引号字面量的命令错拆后误放行。
    """
    segments: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if ch == "\\":
            # 转义：反斜杠 + 下一个字符都原样保留，不把下一个字符当操作符
            buf.append(ch)
            if i + 1 < n:
                buf.append(command[i + 1])
                i += 2
            else:
                i += 1
            continue
        if ch == "'":
            j = command.find("'", i + 1)
            if j == -1:
                buf.append(command[i:])
                break
            buf.append(command[i : j + 1])
            i = j + 1
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if command[j] == "\\":
                    j += 2
                    continue
                if command[j] == '"':
                    break
                j += 1
            if j >= n:
                buf.append(command[i:])
                break
            buf.append(command[i : j + 1])
            i = j + 1
            continue
        # 顶层操作符：&& 和 || 优先于单个 & / |
        if ch == "&" and i + 1 < n and command[i + 1] == "&":
            segments.append("".join(buf).strip())
            buf.clear()
            i += 2
            continue
        if ch == "|" and i + 1 < n and command[i + 1] == "|":
            segments.append("".join(buf).strip())
            buf.clear()
            i += 2
            continue
        if ch in (";", "|", "&"):
            segments.append("".join(buf).strip())
            buf.clear()
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf).strip())
    return [s for s in segments if s]


def _is_safe_single_command(command: str) -> bool:
    """单条命令（不含顶层操作符）是否在只读白名单内。"""
    c = command.strip()
    if not c:
        return False
    for safe in _SAFE_COMMANDS:
        if c == safe or c.startswith(safe + " "):
            return not _has_execution_semantics(c)
    return False


def is_safe_command(command: str) -> bool:
    trimmed = command.strip()
    if not trimmed:
        return False
    # 命令替换 / 重定向：写文件或注入执行，任何位置出现都拒绝，不参与拆分。
    for ch in _UNSPLITTABLE_OPERATORS:
        if ch in trimmed:
            return False
    # 按顶层 && / || / ; / | / & 拆分，每一段都必须是只读单命令。
    # 逐段独立过白名单（含 _has_execution_semantics），因此 `grep x f && rm -rf /`
    # 会因 rm 段不安全而整体拒绝——安全边界不因拆分而松动。
    for segment in _split_compound(trimmed):
        if not _is_safe_single_command(segment):
            return False
    return True


class DangerousCommandDetector:
    def __init__(self, extra_patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex_str, reason in extra_patterns:
                self._patterns.append((re.compile(regex_str), reason))

    def detect(self, command: str) -> tuple[bool, str]:
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""
