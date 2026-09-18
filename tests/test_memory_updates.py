"""版本化記憶、作廢、隔離與成本控制的回歸測試。"""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3

import core.ai.memory_manager as memory_manager
import database.repository.memory_repository as repo
from database.ai.sqlite import get_connection


def _run(coro):
    return asyncio.run(coro)


def _hash(content: str) -> str:
    normalized = " ".join(content.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def test_conversation_turn_is_saved_user_before_assistant(fresh_db):
    async def _test():
        user_id, assistant_id = await memory_manager.save_conversation_turn(
            "u1", "使用者訊息", "AI 回覆", "channel-a", "turn-1",
        )
        assert user_id < assistant_id
        assert await repo.get_recent_messages("u1", "channel-a", 2) == [
            ("user", "使用者訊息"),
            ("assistant", "AI 回覆"),
        ]

    _run(_test())


def test_single_value_personal_memory_replaces_old_value_across_channels(fresh_db):
    async def _test():
        first = "使用者希望被稱為「小明」"
        second = "使用者希望被稱為「小華」"
        await memory_manager.save_memory(
            "u1", "暱稱", first, 5, "channel-a",
            category="identity", subject="nickname", confidence=0.99,
            source_excerpt="叫我小明", content_hash=_hash(first),
            single_value=True, scope_type="user",
        )
        await memory_manager.save_memory(
            "u1", "暱稱", second, 5, "channel-b",
            category="identity", subject="nickname", confidence=0.99,
            source_excerpt="改叫我小華", content_hash=_hash(second),
            single_value=True, scope_type="user",
        )

        records = await repo.get_memory_records("u1", "channel-a")
        assert [item["content"] for item in records] == [second]

        conn = get_connection()
        statuses = conn.execute(
            "SELECT content, status FROM memories WHERE user_id = ? ORDER BY id",
            ("u1",),
        ).fetchall()
        conn.close()
        assert [(row["content"], row["status"]) for row in statuses] == [
            (first, "superseded"),
            (second, "active"),
        ]

    _run(_test())


def test_channel_memory_is_shared_in_channel_but_isolated_elsewhere(fresh_db):
    async def _test():
        content = "本頻道決定週五舉辦活動"
        await memory_manager.save_memory(
            "u1", "頻道活動", content, 5, "channel-a",
            category="decision", subject="friday-event", confidence=0.99,
            source_excerpt="週五舉辦活動", content_hash=_hash(content),
            single_value=True, scope_type="channel",
        )

        same_channel = await repo.get_memory_records("u2", "channel-a")
        other_channel = await repo.get_memory_records("u2", "channel-b")
        assert [item["content"] for item in same_channel] == [content]
        assert same_channel[0]["source_user_id"] == "u1"
        assert other_channel == []

    _run(_test())


def test_personal_memory_follows_user_across_channels_not_other_users(fresh_db):
    async def _test():
        content = "使用者希望被稱為「小明」"
        await memory_manager.save_memory(
            "u1", "暱稱", content, 5, "channel-a",
            category="identity", subject="nickname", confidence=0.99,
            source_excerpt="叫我小明", content_hash=_hash(content),
            single_value=True, scope_type="user",
        )

        across_channel = await repo.get_memory_records("u1", "channel-b")
        other_user = await repo.get_memory_records("u2", "channel-a")
        assert [item["content"] for item in across_channel] == [content]
        assert other_user == []

    _run(_test())


def test_previous_item_retraction_targets_previous_user_message(fresh_db):
    async def _test():
        first_user_id, _ = await memory_manager.save_conversation_turn(
            "u1", "以後叫我小明", "好的", "channel-a", "turn-1",
        )
        assert await memory_manager._extract_deterministic_identity(
            "u1", "以後叫我小明", "channel-a", first_user_id,
        )
        cancel_user_id, _ = await memory_manager.save_conversation_turn(
            "u1", "前項作廢", "了解", "channel-a", "turn-2",
        )

        retracted = await repo.retract_memories_from_previous_user_message(
            "u1", "channel-a", cancel_user_id,
        )
        assert len(retracted) == 1
        assert await repo.get_memory_records("u1", "channel-a") == []

    _run(_test())


def test_forget_slot_removes_memory_and_vector_from_retrieval(fresh_db):
    async def _test():
        content = "使用者希望被稱為「小明」"
        memory_id = await memory_manager.save_memory(
            "u1", "暱稱", content, 5, "channel-a",
            category="identity", subject="nickname", confidence=0.99,
            source_excerpt="叫我小明", content_hash=_hash(content),
            single_value=True, scope_type="user",
        )
        await repo.upsert_vector(
            "u1", "暱稱", content, [1.0, 0.0], 5, "",
            memory_id=memory_id, content_hash=_hash(content),
            embedding_model="test-embedding",
        )

        assert await repo.forget_memory_slot(
            "u1", "channel-a", "identity", "nickname",
        ) == [memory_id]
        assert await repo.get_memory_records("u1", "channel-a") == []
        assert await repo.get_all_vectors("u1", "channel-a") == []

    _run(_test())


def test_unchanged_memory_does_not_need_embedding_twice(fresh_db):
    async def _test():
        content = "使用者偏好繁體中文"
        memory_id = await memory_manager.save_memory(
            "u1", "語言偏好", content, 4, "channel-a",
            category="preference", subject="language", confidence=0.95,
            source_excerpt="我偏好繁體中文", content_hash=_hash(content),
            single_value=True, scope_type="channel",
        )
        pending = await repo.get_memories_needing_vectors("u1", "channel-a")
        assert [item["id"] for item in pending] == [memory_id]

        await repo.upsert_vector(
            "u1", "語言偏好", content, [1.0, 0.0], 4, "channel-a",
            memory_id=memory_id, content_hash=_hash(content),
            embedding_model="test-embedding",
        )
        assert await repo.get_memories_needing_vectors("u1", "channel-a") == []

    _run(_test())


def test_medium_confidence_memory_requires_repeated_confirmation(fresh_db):
    async def _test():
        content = "使用者偏好安靜的環境"
        kwargs = dict(
            category="preference", subject="environment", confidence=0.7,
            source_excerpt="我偏好安靜的環境", content_hash=_hash(content),
            single_value=False, scope_type="user",
        )
        first_id = await memory_manager.save_memory(
            "u1", "環境偏好", content, 3, "channel-a", **kwargs,
        )
        assert await repo.get_memory_records("u1", "channel-a") == []

        second_id = await memory_manager.save_memory(
            "u1", "環境偏好", content, 3, "channel-a", **kwargs,
        )
        assert second_id == first_id
        records = await repo.get_memory_records("u1", "channel-a")
        assert len(records) == 1
        assert records[0]["status"] == "active"
        assert records[0]["confidence"] >= 0.85

    _run(_test())


def test_deterministic_nickname_parser_updates_slot(fresh_db):
    async def _test():
        assert await memory_manager._extract_deterministic_identity(
            "u1", "我的暱稱是 ooxxooxsjhuehbu", "channel-a", 10,
        )
        assert await memory_manager._extract_deterministic_identity(
            "u1", "暱稱改成 guyehjiuyhj", "channel-a", 20,
        )
        records = await repo.get_memory_records("u1", "channel-a")
        assert len(records) == 1
        assert "guyehjiuyhj" in records[0]["content"]
        assert records[0]["subject"] == "nickname"

    _run(_test())


def test_legacy_memory_schema_migrates_without_losing_rows(tmp_path, monkeypatch):
    import database.ai.sqlite as sqlite_mod

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            keyword TEXT NOT NULL,
            content TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL DEFAULT 1,
            UNIQUE(user_id, keyword)
        );
        CREATE TABLE vector_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            keyword TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding TEXT NOT NULL,
            importance INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL DEFAULT 1,
            UNIQUE(user_id, keyword)
        );
        INSERT INTO memories (user_id, keyword, content, importance)
        VALUES ('u1', '偏好', '喜歡安靜', 4);
        INSERT INTO vector_memories (
            user_id, keyword, content, embedding, importance
        ) VALUES ('u1', '偏好', '喜歡安靜', '[1.0, 0.0]', 4);
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(sqlite_mod, "_DB", db_path)
    repo.init_tables()

    conn = get_connection()
    row = conn.execute(
        "SELECT content, status, content_hash, scope_type "
        "FROM memories WHERE user_id = 'u1'"
    ).fetchone()
    columns = {
        item["name"] for item in conn.execute("PRAGMA table_info(memories)")
    }
    conn.close()
    assert row["content"] == "喜歡安靜"
    assert row["status"] == "active"
    assert row["content_hash"]
    assert {"scope_type", "channel_id", "confidence", "source_message_id"} <= columns
    assert row["scope_type"] == "channel"


def test_incremental_summary_waits_for_enough_new_messages(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_count(user_id, channel_id):
        return 100

    async def fake_state(user_id, channel_id):
        return {
            "summary": "既有摘要",
            "msg_count": 90,
            "last_message_id": 90,
            "updated_at": 1,
        }

    async def fake_messages(user_id, channel_id, after_id, *, exclude_recent):
        captured["args"] = (user_id, channel_id, after_id, exclude_recent)
        return [
            {"id": 91 + index, "role": "user", "content": f"新增 {index}"}
            for index in range(5)
        ]

    monkeypatch.setattr(memory_manager.repo, "count_messages", fake_count)
    monkeypatch.setattr(memory_manager.repo, "get_summary_state", fake_state)
    monkeypatch.setattr(memory_manager.repo, "get_messages_after", fake_messages)

    _run(memory_manager._summarize_if_needed("u1", "channel-a"))
    assert captured["args"] == ("u1", "channel-a", 90, 10)
