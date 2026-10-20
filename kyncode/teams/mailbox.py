from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kyncode.retry import BackoffConfig, retry_sync

# 等待文件锁的总时限（秒）。到点抛出异常交给调用方处理，不能悄悄把消息扔掉。
LOCK_ACQUIRE_TIMEOUT = 5.0
# 超过这个时长的锁文件视为持有者已经崩溃，可以强行接管。
STALE_LOCK_AGE = 10.0


@dataclass
class MailboxMessage:
    # from 是 Python 保留字，属性名用 from_agent，落盘时 JSON key 仍是 from
    from_agent: str
    text: str
    timestamp: str = ""
    read: bool = False
    # 结构化消息用这三个字段，普通文本消息留空
    type: str = "text"
    request_id: str = ""
    approve: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "from": self.from_agent,
            "text": self.text,
            "timestamp": self.timestamp,
            "read": self.read,
        }
        # 普通文本消息不写这三个字段，磁盘上只留必要的键
        if self.type and self.type != "text":
            d["type"] = self.type
        if self.request_id:
            d["requestId"] = self.request_id
        if self.approve is not None:
            d["approve"] = self.approve
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MailboxMessage:
        return cls(
            from_agent=data.get("from", ""),
            text=data.get("text", ""),
            timestamp=data.get("timestamp", ""),
            read=data.get("read", False),
            type=data.get("type", "text"),
            request_id=data.get("requestId", ""),
            approve=data.get("approve"),
        )


class Mailbox:
    """Single-file mailbox with file locking, one JSON array per agent.

    Each agent's inbox is stored as ``{agent_id}.json`` under *base_dir*.
    A companion ``.lock`` file is used for mutual exclusion.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        # 同进程内的并发直接用内存锁串行化，文件锁只负责隔离独立进程的 teammate。
        # 省掉一轮文件系统争抢，也避免同进程的线程互相把重试预算耗光。
        self._mu = threading.Lock()

    # ── path helpers ─────────────────────────────────────────────

    def _inbox_path(self, agent_id: str) -> Path:
        return self._base_dir / f"{agent_id}.json"

    def _lock_path(self, agent_id: str) -> Path:
        return self._base_dir / f"{agent_id}.json.lock"

    # ── file lock ────────────────────────────────────────────────

    def _with_lock(
        self,
        agent_id: str,
        fn: callable,
    ) -> Any:
        """获取文件锁，读取收件箱，应用变更后写回。"""
        lock_file = self._lock_path(agent_id)

        with self._mu:
            # 抢文件锁：通用退避重试（full jitter + 总 deadline 5s）。stale 接管
            # 是锁语义（非退避语义），保留在 _acquire_lock 内层循环里。
            try:
                retry_sync(
                    lambda: self._acquire_lock(lock_file),
                    config=BackoffConfig(
                        base=0.005,
                        cap=0.08,
                        max_retries=None,
                        deadline=LOCK_ACQUIRE_TIMEOUT,
                    ),
                    retryable=lambda exc: isinstance(exc, FileExistsError),
                    scope="mailbox_lock",
                )
            except FileExistsError:
                raise TimeoutError(
                    f"mailbox {agent_id}: 等待文件锁超过 {LOCK_ACQUIRE_TIMEOUT}s，消息未写入"
                )

            try:
                messages = self._read_inbox(agent_id)
                messages = fn(messages)
                self._write_inbox(agent_id, messages)
            finally:
                lock_file.unlink(missing_ok=True)

    def _acquire_lock(self, lock_file: Path) -> None:
        """原子创建锁文件；stale 时强制接管（立即重试），否则抛 FileExistsError 交给退避层。"""
        while True:
            try:
                fd = os.open(
                    str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                )
                os.close(fd)
                return
            except FileExistsError:
                # 锁被别人持有，先看它是不是已经陈旧到可以接管
                try:
                    info = lock_file.stat()
                    if time.time() - info.st_mtime > STALE_LOCK_AGE:
                        lock_file.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                raise

    # ── inbox I/O ────────────────────────────────────────────────

    def _read_inbox(self, agent_id: str) -> list[MailboxMessage]:
        path = self._inbox_path(agent_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [MailboxMessage.from_dict(item) for item in data]
        except (json.JSONDecodeError, KeyError, TypeError):
            return []

    def _write_inbox(self, agent_id: str, messages: list[MailboxMessage]) -> None:
        path = self._inbox_path(agent_id)
        data = json.dumps(
            [m.to_dict() for m in messages],
            ensure_ascii=False,
            indent=2,
        )
        path.write_text(data, encoding="utf-8")

    # ── public API ───────────────────────────────────────────────

    def write(self, agent_id: str, message: MailboxMessage) -> None:
        """Append a message to *agent_id*'s inbox (thread-safe)."""

        def _append(msgs: list[MailboxMessage]) -> list[MailboxMessage]:
            message.read = False
            if not message.timestamp:
                message.timestamp = datetime.now(UTC).isoformat()
            msgs.append(message)
            return msgs

        self._with_lock(agent_id, _append)

    def read(self, agent_id: str) -> list[MailboxMessage]:
        """Return all unread messages without marking them as read."""
        messages = self._read_inbox(agent_id)
        return [m for m in messages if not m.read]

    def consume(self, agent_id: str) -> list[MailboxMessage]:
        """Return all unread messages and mark them as read (thread-safe)."""
        result: list[MailboxMessage] = []

        def _mark_read(msgs: list[MailboxMessage]) -> list[MailboxMessage]:
            for m in msgs:
                if not m.read:
                    result.append(m)
                    m.read = True
            return msgs

        self._with_lock(agent_id, _mark_read)
        return result

    def broadcast(
        self,
        team_members: list[str],
        message: MailboxMessage,
        exclude: str = "",
    ) -> None:
        for agent_id in team_members:
            if agent_id == exclude:
                continue
            self.write(agent_id, message)

    def cleanup(self, agent_id: str) -> None:
        """Remove an agent's inbox file."""
        self._inbox_path(agent_id).unlink(missing_ok=True)
        self._lock_path(agent_id).unlink(missing_ok=True)

    def cleanup_all(self) -> None:
        """Remove all inbox files."""
        if not self._base_dir.exists():
            return
        for f in self._base_dir.iterdir():
            f.unlink(missing_ok=True)


def create_message(
    from_agent: str,
    text: str,
    message_type: str = "text",
    request_id: str = "",
    approve: bool | None = None,
) -> MailboxMessage:
    return MailboxMessage(
        from_agent=from_agent,
        text=text,
        timestamp=datetime.now(UTC).isoformat(),
        type=message_type,
        request_id=request_id,
        approve=approve,
    )
