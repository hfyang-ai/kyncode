from __future__ import annotations

import re

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
    """Detect execution / write semantics hidden in whitelisted commands.

    awk's system() calls the shell; find's -exec/-execdir/-delete/-ok executes or
    deletes files; sed's -i rewrites files in place. A command name on the safe
    list must not become an arbitrary-code-execution entry point.
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


# Command substitution / redirection: these write files or inject execution, so
# any occurrence rejects the whole command without splitting. ``<`` is
# intentionally absent: read-only commands like ``cat < f`` were historically
# allowed, and the command after ``<`` is still validated by
# _is_safe_single_command (``sh < script`` is rejected because sh is not safe).
_UNSPLITTABLE_OPERATORS = (">", "$(", "`")


def _split_compound(command: str) -> list[str]:
    """Split on top-level (unquoted, unescaped) && / || / ; / | / & operators.

    Operators inside quotes or backslash escapes are not separators:
    ``grep "a|b"``'s ``|`` is a regex and ``sed '1,10p;20p'``'s ``;`` is an
    address separator. Parsing is deliberately conservative: an unterminated
    quote makes the rest of the string one segment, so a mis-split cannot
    accidentally auto-allow part of a command.
    """
    segments: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if ch == "\\":
            # Escape: keep the backslash and the next character verbatim so the
            # next character is never treated as an operator.
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
        # Top-level operators: && and || take precedence over single & / |.
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
    """Whether a single command (no top-level operator) is on the read-only list."""
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
    # Command substitution / redirection: write or inject, reject without splitting.
    for ch in _UNSPLITTABLE_OPERATORS:
        if ch in trimmed:
            return False
    # Split on top-level && / || / ; / | / & and require every segment to be
    # read-only. Each segment is checked independently (including
    # _has_execution_semantics), so ``grep x f && rm -rf /`` is rejected as a
    # whole because the rm segment is unsafe: splitting never loosens the guard.
    for segment in _split_compound(trimmed):
        if not _is_safe_single_command(segment):
            return False
    return True
