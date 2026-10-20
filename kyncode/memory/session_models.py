from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from kyncode.conversation import (
    Message,
    ToolResultBlock,
    ToolUseBlock,
)

# 压缩边界记录的类型标记。普通对话消息不带 type，靠 role 区分 user / assistant。
TYPE_COMPACT_BOUNDARY = "compact_boundary"


# ---------------------------------------------------------------------------
# SessionRecord
# ---------------------------------------------------------------------------


def _tool_uses_to_dicts(message: Message) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tu in message.tool_uses or []:
        d: dict[str, Any] = {"tool_use_id": tu.tool_use_id, "tool_name": tu.tool_name}
        # arguments 为空时整个键省略，纯文本消息落盘不带多余字段
        if tu.arguments:
            d["arguments"] = tu.arguments
        out.append(d)
    return out


def _tool_results_to_dicts(message: Message) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tr in message.tool_results or []:
        d: dict[str, Any] = {"tool_use_id": tr.tool_use_id, "content": tr.content}
        # is_error 为 False 时省略，成功的工具结果不留 "is_error":false 这种噪音
        if tr.is_error:
            d["is_error"] = tr.is_error
        out.append(d)
    return out


@dataclass
class SessionRecord:
    """一条落盘的会话记录。

    工具块以与协议无关的内部表示内联存储（tool_use_id / tool_name / arguments），
    换 provider 恢复会话时也能还原；两者为空时不写入字典。普通消息 type 为 None，
    靠 role 区分；type 为 "compact_boundary" 时 content 是压缩边界的结构化载荷。

    本类只负责对象与后端无关字典之间的转换，文件格式由存储层决定。
    """

    role: str
    content: Any
    timestamp: datetime
    type: str | None = None
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def is_compact_boundary(self) -> bool:
        return self.type == TYPE_COMPACT_BOUNDARY

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
            "ts": int(self.timestamp.timestamp()),
        }
        if self.type:
            data["type"] = self.type
        if self.tool_uses:
            data["tool_uses"] = self.tool_uses
        if self.tool_results:
            data["tool_results"] = self.tool_results
        return data

    @classmethod
    def from_dict(cls, data: Any) -> SessionRecord | None:
        if not isinstance(data, dict) or "role" not in data:
            # 旧格式（以 type 区分 user/assistant/tool_result）或损坏数据安全跳过
            return None
        ts_raw = data.get("ts")
        if isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(ts_raw, tz=UTC)
        else:
            ts = datetime.now(UTC)
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            timestamp=ts,
            type=data.get("type"),
            tool_uses=data.get("tool_uses") or [],
            tool_results=data.get("tool_results") or [],
        )

    @classmethod
    def from_message(cls, message: Message) -> list[SessionRecord]:
        # 一条 Message 对应一条记录，工具块内联其中；思考块不落盘：它的
        # signature 只在同一轮工具循环内需要回传，跨会话恢复用不上。
        return [
            cls(
                role=message.role,
                content=message.content,
                timestamp=datetime.now(UTC),
                tool_uses=_tool_uses_to_dicts(message),
                tool_results=_tool_results_to_dicts(message),
            )
        ]

    def to_message(self) -> Message:
        content = self.content if isinstance(self.content, str) else ""
        return Message(
            role=self.role,
            content=content,
            # 恢复时按内容前缀探测 system_reminder 标记：旧记录没有这个字段，
            # 靠 <system-reminder> 标签识别，保证 resume 后仍能按协议路由。
            system_reminder=content.lstrip().startswith("<system-reminder>"),
            tool_uses=[
                ToolUseBlock(
                    tool_use_id=tu.get("tool_use_id", ""),
                    tool_name=tu.get("tool_name", ""),
                    arguments=tu.get("arguments", {}),
                )
                for tu in self.tool_uses
            ],
            tool_results=[
                ToolResultBlock(
                    tool_use_id=tr.get("tool_use_id", ""),
                    content=tr.get("content", ""),
                    is_error=tr.get("is_error", False),
                )
                for tr in self.tool_results
            ],
        )


# ---------------------------------------------------------------------------
# Compact boundary 载荷（摘要 + 内联的 keep 尾部）
# ---------------------------------------------------------------------------


def _message_to_keep_dict(message: Message) -> dict[str, Any]:
    """将一条保留的尾巴消息序列化成压缩边界里内联的 dict，格式与普通记录一致。"""
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    tool_uses = _tool_uses_to_dicts(message)
    tool_results = _tool_results_to_dicts(message)
    if tool_uses:
        data["tool_uses"] = tool_uses
    if tool_results:
        data["tool_results"] = tool_results
    return data


def make_compact_boundary(summary: str, keep: list[Message]) -> SessionRecord:
    """构建一条 compact_boundary 记录，内联摘要和原样保留的 keep 尾部。

    `keep` 是 auto_compact 原样保留的近期尾部消息，连同工具块一起内联进 boundary，
    压缩后恢复会话时这段尾巴才不会缺掉调用链。boundary 之前的原始前缀保留在磁盘上
    但不会被重放。
    """
    keep_dicts = [_message_to_keep_dict(msg) for msg in keep]
    payload = {"summary": summary, "keep": keep_dicts}
    return SessionRecord(
        role="system",
        content=payload,
        timestamp=datetime.now(UTC),
        type=TYPE_COMPACT_BOUNDARY,
    )


def parse_compact_boundary(record: SessionRecord) -> tuple[str, list[Message]]:
    """make_compact_boundary 的逆操作：返回 (summary, keep_messages)。

    对遗留或格式异常的 payload 降级返回 ("", [])，确保单条损坏的 boundary
    不会导致 resume 崩溃。
    """
    content = record.content
    if not isinstance(content, dict):
        return "", []
    summary = content.get("summary", "")
    keep_raw = content.get("keep", [])
    keep_messages: list[Message] = []
    for item in keep_raw if isinstance(keep_raw, list) else []:
        if not isinstance(item, dict) or "role" not in item:
            continue
        keep_messages.append(
            SessionRecord(
                role=item["role"],
                content=item.get("content", ""),
                timestamp=record.timestamp,
                tool_uses=item.get("tool_uses") or [],
                tool_results=item.get("tool_results") or [],
            ).to_message()
        )
    return summary, keep_messages


# ---------------------------------------------------------------------------
# Record ↔ Message 转换
# ---------------------------------------------------------------------------


# 压缩摘要在恢复会话时作为一条 user 消息重放，前缀说明这段是早期对话的浓缩。
RESUME_SUMMARY_PREFIX = (
    "本次会话延续自之前的对话，因上下文空间不足进行了压缩。以下是早期对话的摘要：\n\n"
)


def records_to_messages(records: list[SessionRecord]) -> list[Message]:
    """把落盘记录还原成内存中的对话消息。

    每条记录对应一条消息，工具块随记录一起还原；压缩边界展开成「摘要 user 消息 +
    原样保留的 keep 尾部」。恢复出来的历史可能含中断留下的悬空 tool_use，交由发
    请求前的 ensure_tool_pairing 统一补齐，这里不做截断。
    """
    messages: list[Message] = []
    for record in records:
        if record.is_compact_boundary():
            summary, keep_messages = parse_compact_boundary(record)
            messages.append(
                Message(role="user", content=RESUME_SUMMARY_PREFIX + summary)
            )
            messages.extend(keep_messages)
            continue
        if record.role not in ("user", "assistant"):
            continue
        messages.append(record.to_message())
    return messages


# ---------------------------------------------------------------------------
# SessionMeta
# ---------------------------------------------------------------------------


@dataclass
class SessionMeta:
    id: str
    title: str = ""
    summary: str = ""
    message_count: int = 0
    total_tokens: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_active: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "message_count": self.message_count,
            "total_tokens": self.total_tokens,
            "created_at": self.created_at.isoformat(),
            "last_active": self.last_active.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Any) -> SessionMeta | None:
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                id=data["id"],
                title=data.get("title", ""),
                summary=data.get("summary", ""),
                message_count=data.get("message_count", 0),
                total_tokens=data.get("total_tokens", 0),
                created_at=datetime.fromisoformat(data["created_at"]),
                last_active=datetime.fromisoformat(data["last_active"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
