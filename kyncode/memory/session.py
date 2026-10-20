from __future__ import annotations

import random
import string
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from kyncode.conversation import ConversationManager, Message
from kyncode.memory.session_models import (
    SessionMeta,
    SessionRecord,
    records_to_messages,
)
from kyncode.memory.session_store import SessionStore, create_session_store

DEFAULT_MAX_AGE_DAYS = 30
TITLE_MAX_LENGTH = 50

SESSION_SUMMARY_PROMPT = (
    "你是一个对话摘要助手。请根据下面的对话内容，用一句话总结这个会话的主要内容。"
    "只输出摘要文本，不要加任何前缀或标点符号外的修饰。不要调用任何工具。"
)


# ---------------------------------------------------------------------------
# Session（活跃会话句柄）
# ---------------------------------------------------------------------------


class Session:
    def __init__(
        self,
        session_id: str,
        meta: SessionMeta,
        store: SessionStore,
    ) -> None:
        self.session_id = session_id
        self.meta = meta
        self._store = store

    def append(self, message: Message) -> None:
        for record in SessionRecord.from_message(message):
            self._store.append(self.session_id, record)

        self.meta.message_count += 1
        self.meta.last_active = datetime.now(UTC)

        if not self.meta.title and message.role == "user" and message.content:
            self.meta.title = message.content[:TITLE_MAX_LENGTH]

        self.save_meta()

    def append_record(self, record: SessionRecord) -> None:
        """追加一条原始 SessionRecord（例如 compact_boundary 标记）。

        与 append() 不同，此方法不会更新 message_count/title——boundary 是
        结构性标记而非对话轮次。last_active 仍会更新，以保证 session 按最近
        使用排序。
        """
        self._store.append(self.session_id, record)
        self.meta.last_active = datetime.now(UTC)
        self.save_meta()

    def save_meta(self) -> None:
        self._store.save_meta(self.meta)

    def close(self) -> None:
        self._store.close(self.session_id)


# ---------------------------------------------------------------------------
# ResumeResult
# ---------------------------------------------------------------------------


@dataclass
class ResumeResult:
    session: Session
    messages: list[Message]
    last_active: datetime


# ---------------------------------------------------------------------------
# Session 摘要生成
# ---------------------------------------------------------------------------


async def generate_session_summary(
    client: Any, conversation: ConversationManager, protocol: str
) -> str:
    from kyncode.tools.base import StreamEnd, TextDelta

    recent = conversation.history[-10:]
    if not recent:
        return ""

    summary_conv = ConversationManager()
    summary_conv.history = [Message(role="user", content=SESSION_SUMMARY_PROMPT)]
    for msg in recent:
        summary_conv.history.append(msg)
    summary_conv.history.append(
        Message(role="user", content="请用一句话总结上面的对话内容。不要调用工具。")
    )

    collected = ""
    try:
        async for event in client.stream(summary_conv, system=SESSION_SUMMARY_PROMPT):
            if isinstance(event, TextDelta):
                collected += event.text
            elif isinstance(event, StreamEnd):
                pass
    except Exception:
        return ""

    return collected.strip()


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


def _generate_session_id() -> str:
    now = datetime.now()
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"session_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


class SessionManager:
    def __init__(self, work_dir: str, store: SessionStore | None = None) -> None:
        self._store = store if store is not None else create_session_store(work_dir)

    @property
    def store(self) -> SessionStore:
        return self._store

    def create(self) -> Session:
        session_id = _generate_session_id()
        meta = SessionMeta(id=session_id)
        self._store.create(meta)
        return Session(session_id=session_id, meta=meta, store=self._store)

    def list(self) -> list[SessionMeta]:
        metas = self._store.list_metas()
        metas.sort(key=lambda m: m.last_active, reverse=True)
        return metas

    def resume(self, session_id: str) -> ResumeResult | None:
        meta = self._store.load_meta(session_id)
        if meta is None:
            return None

        records = self._store.read_records(session_id)
        if records is None:
            return None

        # 重建压缩后的状态：仅从最后一个 compact_boundary 开始重放。
        # 该标记之前的 record 是已被摘要过的原始前缀——保留在磁盘上供审计，
        # 但不再重放。标记本身内联了摘要 + 原样 keep 尾部，标记之后追加的
        # 普通消息（续写）照常重放。没有 boundary 则全量重放（兼容旧 session）。
        last_boundary = -1
        for i, rec in enumerate(records):
            if rec.is_compact_boundary():
                last_boundary = i
        if last_boundary >= 0:
            records = records[last_boundary:]

        return ResumeResult(
            session=Session(session_id=session_id, meta=meta, store=self._store),
            messages=records_to_messages(records),
            last_active=meta.last_active,
        )

    def delete(self, session_id: str) -> bool:
        return self._store.delete(session_id)

    def cleanup(self, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
        removed = 0

        for meta in self.list():
            if meta.last_active < cutoff and self.delete(meta.id):
                removed += 1

        return removed
