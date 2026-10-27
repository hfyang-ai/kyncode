from __future__ import annotations

import re

# Dangerous command patterns, checked in order. The first regex that matches
# wins, so this list's order is observable behavior: keep it stable.
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # File deletion
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
    # Disk operations
    (re.compile(r"mkfs\."), "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/"), "直接写磁盘设备"),
    (re.compile(r">\s*/dev/(sd|nvme|disk|mmcblk)"), "覆盖磁盘设备"),
    # Permission changes
    (re.compile(r"chmod\s+-R\s+777\s+/"), "递归修改根目录权限为 777"),
    (re.compile(r"chown\s+-R\s+\S+\s+/(?:\s|$)"), "递归修改根目录所有权"),
    # System shutdown
    (re.compile(r"\b(shutdown|reboot|halt|poweroff|init\s+[06])\b"), "系统关机或重启"),
    (re.compile(r"systemctl\s+(poweroff|reboot|halt)\b"), "systemctl 关机或重启"),
    # Network / injection
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh"), "管道执行远程脚本(curl)"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh"), "管道执行远程脚本(wget)"),
    # Destructive git operations
    (
        re.compile(r"git\s+push\s+.*--force(?!-with-lease)"),
        "强制推送（非 --force-with-lease）",
    ),
    (re.compile(r"git\s+reset\s+--hard\b"), "硬重置（丢弃所有未提交更改）"),
    (re.compile(r"git\s+clean\s+-[a-z]*f[a-z]*d[a-z]*x[a-z]*\b"), "强力清理未跟踪文件"),
    # Overwriting critical system files
    (re.compile(r">\s*/etc/(passwd|shadow|sudoers|group)\b"), "覆盖关键系统文件"),
    (re.compile(r"cp\s+/dev/null\s+/etc/(passwd|shadow|sudoers)"), "清空关键系统文件"),
    # Docker destruction
    (
        re.compile(r"docker\s+(rm|container\s+rm)\s+-f\s+.*\$"),
        "强制删除命令替换结果中的所有容器",
    ),
    (
        re.compile(r"docker\s+system\s+prune\s+-[a-z]*f"),
        "强制清理所有未使用的 Docker 资源",
    ),
    # Firewall
    (re.compile(r"iptables\s+-F\b"), "清除所有 iptables 防火墙规则"),
    # Home-directory variants via variables / long options
    (
        re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+\$HOME(?:\s|$)"),
        "递归强制删除家目录($HOME)",
    ),
    (
        re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+\$\{HOME\}(?:\s|$)"),
        "递归强制删除家目录(${HOME})",
    ),
    (re.compile(r"rm\s+.*--recursive[^\s]*\s+--force\b"), "长选项递归强制删除"),
    # find execution / bulk deletion
    (
        re.compile(r"find\s+.*-(?:exec|execdir)\s+(?:rm|sh|bash)\b"),
        "find 执行删除或脚本",
    ),
    (re.compile(r"find\s+.*-delete\b"), "find 批量删除文件"),
    # Encoded / arbitrary command piped to a shell
    (re.compile(r"base64\s+.*-d[^\s]*\s*\|\s*(?:ba|z)?sh\b"), "base64 解码后管道执行"),
    (re.compile(r"\|\s*(?:ba|z)?sh\b"), "管道执行 shell 脚本"),
]
