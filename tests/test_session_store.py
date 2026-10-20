from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kyncode.conversation import Message
from kyncode.memory.session import SessionManager
from kyncode.memory.session_models import SessionMeta, SessionRecord
from kyncode.memory.session_store import (
    JsonlSessionStore,
    SessionStore,
    create_session_store,
)


def _meta(session_id: str = "session_test") -> SessionMeta:
    return SessionMeta(id=session_id)


def _store(tmp_path: Path) -> JsonlSessionStore:
    return JsonlSessionStore(tmp_path / ".kyncode" / "sessions")


class TestJsonlSessionStore:
    def test_create_writes_meta_and_empty_jsonl(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        meta = _meta()

        store.create(meta)

        sessions_dir = tmp_path / ".kyncode" / "sessions"
        assert (sessions_dir / "session_test.jsonl").exists()
        assert (sessions_dir / "session_test.jsonl").read_text(encoding="utf-8") == ""
        loaded = store.load_meta("session_test")
        assert loaded is not None
        assert loaded.id == "session_test"

    def test_append_is_visible_immediately(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        meta = _meta()
        store.create(meta)

        record = SessionRecord.from_message(
            Message(role="user", content="hello")
        )[0]
        store.append("session_test", record)

        path = tmp_path / ".kyncode" / "sessions" / "session_test.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        # 存储层使用紧凑 JSON，单条记录一行
        assert lines[0] == json.dumps(
            record.to_dict(), ensure_ascii=False, separators=(",", ":")
        )

    def test_meta_file_uses_indented_json(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        meta = SessionMeta(id="session_test", title="标题")
        store.save_meta(meta)

        path = tmp_path / ".kyncode" / "sessions" / "session_test.meta"
        assert path.read_text(encoding="utf-8") == json.dumps(
            meta.to_dict(), ensure_ascii=False, indent=2
        )

    def test_read_records_roundtrip_and_skip_bad_lines(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.create(_meta())
        store.append(
            "session_test",
            SessionRecord.from_message(Message(role="user", content="hello"))[0],
        )

        path = tmp_path / ".kyncode" / "sessions" / "session_test.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n")
            f.write("{bad json\n")
            f.write('{"type": "assistant", "content": "legacy"}\n')

        store.append(
            "session_test",
            SessionRecord.from_message(Message(role="assistant", content="hi"))[0],
        )

        records = store.read_records("session_test")
        assert records is not None
        assert [r.content for r in records] == ["hello", "hi"]

    def test_read_records_missing_session_returns_none(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        assert store.read_records("nope") is None

    def test_load_meta_corrupted_returns_none(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        path = tmp_path / ".kyncode" / "sessions" / "bad.meta"
        path.write_text("not json", encoding="utf-8")
        assert store.load_meta("bad") is None

    def test_list_metas_skips_corrupted(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.create(_meta("session_a"))
        store.create(_meta("session_b"))
        (tmp_path / ".kyncode" / "sessions" / "broken.meta").write_text(
            "nope", encoding="utf-8"
        )

        ids = {m.id for m in store.list_metas()}
        assert ids == {"session_a", "session_b"}

    def test_delete_and_close_are_idempotent(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.create(_meta())
        store.append(
            "session_test",
            SessionRecord.from_message(Message(role="user", content="x"))[0],
        )

        assert store.delete("session_test") is True
        assert store.delete("session_test") is False
        store.close("session_test")
        store.close("session_test")

    def test_append_after_close_reopens(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        store.create(_meta())
        store.close("session_test")

        store.append(
            "session_test",
            SessionRecord.from_message(Message(role="user", content="again"))[0],
        )

        records = store.read_records("session_test")
        assert records is not None
        assert [r.content for r in records] == ["again"]


class FakeSessionStore:
    """内存后端，用于验证上层不依赖文件系统。"""

    def __init__(self) -> None:
        self.metas: dict[str, SessionMeta] = {}
        self.records: dict[str, list[SessionRecord]] = {}
        self.closed: set[str] = set()

    def create(self, meta: SessionMeta) -> None:
        self.metas[meta.id] = meta
        self.records[meta.id] = []

    def append(self, session_id: str, record: SessionRecord) -> None:
        self.records.setdefault(session_id, []).append(record)

    def save_meta(self, meta: SessionMeta) -> None:
        self.metas[meta.id] = meta

    def load_meta(self, session_id: str) -> SessionMeta | None:
        return self.metas.get(session_id)

    def read_records(self, session_id: str) -> list[SessionRecord] | None:
        records = self.records.get(session_id)
        return list(records) if records is not None else None

    def list_metas(self) -> list[SessionMeta]:
        return list(self.metas.values())

    def delete(self, session_id: str) -> bool:
        deleted = self.metas.pop(session_id, None) is not None
        records = self.records.pop(session_id, None)
        return deleted or records is not None

    def close(self, session_id: str) -> None:
        self.closed.add(session_id)


class TestSwappableStore:
    def test_fake_store_satisfies_protocol(self) -> None:
        assert isinstance(FakeSessionStore(), SessionStore)
        assert isinstance(JsonlSessionStore(Path("/tmp")), SessionStore)

    def test_session_manager_works_with_custom_store(self, tmp_path: Path) -> None:
        store = FakeSessionStore()
        mgr = SessionManager(str(tmp_path), store=store)
        assert mgr.store is store

        session = mgr.create()
        sid = session.session_id
        session.append(Message(role="user", content="hello"))
        session.append(Message(role="assistant", content="hi"))
        session.close()

        assert store.closed == {sid}
        assert [m.id for m in mgr.list()] == [sid]

        result = mgr.resume(sid)
        assert result is not None
        assert [m.content for m in result.messages] == ["hello", "hi"]
        assert result.session.meta.message_count == 2

        assert mgr.delete(sid) is True
        assert mgr.resume(sid) is None

    def test_cleanup_with_custom_store(self, tmp_path: Path) -> None:
        store = FakeSessionStore()
        mgr = SessionManager(str(tmp_path), store=store)
        old = mgr.create()
        old.meta.last_active = datetime.now(UTC) - timedelta(days=31)
        old.save_meta()
        fresh = mgr.create()

        removed = mgr.cleanup(max_age_days=30)

        assert removed == 1
        assert store.load_meta(old.session_id) is None
        assert store.load_meta(fresh.session_id) is not None


def test_create_session_store_defaults_to_jsonl(tmp_path: Path) -> None:
    store = create_session_store(str(tmp_path))
    assert isinstance(store, JsonlSessionStore)
    assert store.sessions_dir == tmp_path / ".kyncode" / "sessions"
