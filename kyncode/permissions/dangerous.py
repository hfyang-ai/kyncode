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


def is_safe_command(command: str) -> bool:
    trimmed = command.strip()
    if not trimmed:
        return False
    for ch in ("|", ";", "&&", ">", "$(", "`"):
        if ch in trimmed:
            return False
    for safe in _SAFE_COMMANDS:
        if trimmed == safe or trimmed.startswith(safe + " "):
            return not _has_execution_semantics(trimmed)
    return False


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
