from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 执行轨迹 checkpoint：ToolLedger
#
# 记录每一次「非幂等」工具调用的执行轨迹（executing → 终态），落盘为 append-only
# JSONL。它在会话级（按 session_id）持久化，跨进程崩溃/重启仍然可读。
#
# 它和 Session（对话历史）、FileHistory（编辑前备份）三者各管一段：
#   - Session        记录「模型说了什么、要调什么工具」，resume 时回放对话；
#   - FileHistory    记录「文件改之前长什么样」，/rewind 时回滚代码；
#   - ToolLedger     记录「哪个非幂等工具真的执行了、结果如何」，resume 时
#                    重放已执行的结果，避免重复副作用。
#
# 为什么只记非幂等工具：只读工具（read/network）重跑无副作用，重放它们既没必要，
# 反而可能返回过期的文件内容。写/命令类工具一旦重跑就会重复产生副作用，这正是
# 要防的东西。
#
# 两段写（executing → 终态）解决「执行到一半中断」：执行前先落一条 executing，
# 执行完再落一条终态。resume 时只有 executing、没有终态的调用就是「被打断、结局
# 未知」，既不能断言无副作用，也不能拿去重放——交给上层按 interrupted 兜底。
# ---------------------------------------------------------------------------

# 状态取值。终态集合单独定义，load() 时按它分辨「执行完」和「被打断」。
EXECUTING = "executing"
SUCCEEDED = "succeeded"
FAILED = "failed"
DENIED = "denied"

TERMINAL_STATUSES = frozenset({SUCCEEDED, FAILED, DENIED})
# 可重放（真正执行过、有确定结果）的终态：denied 意味着根本没执行，无副作用，
# 不能拿去重放——重放一条拒绝结果会把用户之后改主意想放行的调用也挡住。
REPLAYABLE_STATUSES = frozenset({SUCCEEDED, FAILED})

LEDGER_SUFFIX = ".ledger.jsonl"


def fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    """给一次工具调用算一个内容指纹：同一工具 + 等价参数 → 同一指纹。

    参数序列化时 sort_keys，消除参数键顺序带来的假差异；default=str 兜住
    个别非 JSON 原生类型，保证任意合法工具参数都能算。
    """
    canonical = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    payload = f"{tool_name}\x00{canonical}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ledger_path(work_dir: str, session_id: str) -> Path:
    """按会话隔离的 ledger 文件路径，和 session jsonl/meta 并排放。

    session_id 为空（一次性调用、测试）时落到 default，与 spill_dir 的约定一致。
    """
    sid = session_id or "default"
    return Path(work_dir) / ".kyncode" / "sessions" / f"{sid}{LEDGER_SUFFIX}"


@dataclass
class LedgerEntry:
    """一条工具调用轨迹记录。

    result 是可选的 dict（{"output", "is_error", "error_code", "truncated"}）：
    executing 阶段还没有结果，result 为 None；终态必带。
    """

    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    fingerprint: str
    status: str
    ts: float = field(default_factory=time.time)
    result: dict[str, Any] | None = None
    elapsed_ms: float = 0.0

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_jsonl(self) -> str:
        data: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "fingerprint": self.fingerprint,
            "status": self.status,
            "ts": self.ts,
        }
        if self.result is not None:
            data["result"] = self.result
        if self.elapsed_ms:
            data["elapsed_ms"] = self.elapsed_ms
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> LedgerEntry | None:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or "tool_call_id" not in data:
            return None
        return cls(
            tool_call_id=data["tool_call_id"],
            tool_name=data.get("tool_name", ""),
            arguments=data.get("arguments") or {},
            fingerprint=data.get("fingerprint", ""),
            status=data.get("status", EXECUTING),
            ts=float(data.get("ts", 0.0)),
            result=data.get("result"),
            elapsed_ms=float(data.get("elapsed_ms", 0.0)),
        )


class ToolLedger:
    """会话级执行轨迹账本：append-only 写、按 tool_call_id 归并读。

    写侧两段式：record_executing（执行前）→ record_outcome（终态）。每次写都 flush，
    保证工具执行完返回结果之前轨迹已经落盘——即使随后进程崩溃，这次副作用也有据可查。

    读侧 load() 把落盘记录归并成两份索引：
      - committed_by_id：tool_call_id → 最近一条终态记录（跨会话补全悬空 tool_use、
        以及按 id 重放）；
      - _replay_pool：fingerprint → 最近一条「可重放」终态记录。只在 load() 时快照，
        用于 resume 后拦截模型重新发起的同一动作（换个新 id 但内容等价）。consume_replay
        命中即弹出，因此一次恢复只重放一次，之后模型真要再跑一次就正常执行。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.committed_by_id: dict[str, LedgerEntry] = {}
        self._replay_pool: dict[str, LedgerEntry] = {}
        self._lock = threading.Lock()
        self._file: Any = None
        # 账本自建父目录：无论经 for_session 还是直接构造，写路径都能直接落地。
        path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_session(cls, work_dir: str, session_id: str) -> ToolLedger:
        return cls(ledger_path(work_dir, session_id))

    def _open(self) -> None:
        if self._file is None:
            self._file = open(self.path, "a", encoding="utf-8")  # noqa: SIM115

    def _append(self, entry: LedgerEntry) -> None:
        with self._lock:
            self._open()
            self._file.write(entry.to_jsonl() + "\n")
            self._file.flush()

    def record_executing(
        self, tool_call_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> None:
        self._append(
            LedgerEntry(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                fingerprint=fingerprint(tool_name, arguments),
                status=EXECUTING,
            )
        )

    def record_outcome(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        result: dict[str, Any] | None,
        status: str,
        elapsed_ms: float,
    ) -> None:
        entry = LedgerEntry(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            fingerprint=fingerprint(tool_name, arguments),
            status=status,
            result=result,
            elapsed_ms=elapsed_ms,
        )
        self._append(entry)
        with self._lock:
            self.committed_by_id[tool_call_id] = entry

    def load(self) -> None:
        """读盘并把记录归并进 committed_by_id 与 _replay_pool。

        同一 tool_call_id 出现多条（executing 后又接终态），后写者胜。只有终态
        进 committed_by_id；_replay_pool 只收「可重放」终态（succeeded/failed），
        供 resume 后的一次性重放。可反复调用（重复 resume），每次以当前磁盘为准。
        """
        committed: dict[str, LedgerEntry] = {}
        replay: dict[str, LedgerEntry] = {}
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = LedgerEntry.from_jsonl(line)
                    if entry is None or not entry.is_terminal:
                        continue
                    committed[entry.tool_call_id] = entry
                    if entry.status in REPLAYABLE_STATUSES:
                        replay[entry.fingerprint] = entry
        except FileNotFoundError:
            pass
        with self._lock:
            self.committed_by_id = committed
            self._replay_pool = replay

    def consume_replay(self, tool_name: str, arguments: dict[str, Any]) -> LedgerEntry | None:
        """命中重放池即弹出返回：一次恢复只重放一次等价动作。

        返回 None 表示没有可重放的记录，调用方按正常路径执行。
        """
        fp = fingerprint(tool_name, arguments)
        with self._lock:
            return self._replay_pool.pop(fp, None)

    def interrupted_ids(self) -> set[str]:
        """找出「只写了 executing、没有终态」的调用 id——即被打断、结局未知的那批。

        它依赖 load() 后的磁盘状态，调用方在需要前先 load()。
        """
        # 终态 id 在 committed_by_id 里；executing 的 id 需回扫磁盘，
        # 简单起见以「load 时没有终态」为判据：这里回读文件里所有 executing 但
        # 无对应终态的 id。
        ids: set[str] = set()
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = LedgerEntry.from_jsonl(line)
                    if entry is None or entry.status != EXECUTING:
                        continue
                    if entry.tool_call_id not in self.committed_by_id:
                        ids.add(entry.tool_call_id)
        except FileNotFoundError:
            pass
        return ids

    def close(self) -> None:
        with self._lock:
            if self._file is not None and not self._file.closed:
                self._file.close()
            self._file = None
