from __future__ import annotations

import asyncio
import sqlite3

import pytest

from core.ai.memory_exporter import render_table_snapshot, render_user_memory
from database.repository.inspection_repository import (
    get_table_snapshot,
    get_user_memory_snapshot,
)


def test_user_memory_export_is_readable_and_hides_embedding(fresh_db) -> None:
    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "INSERT INTO user_profiles (user_id, username, data) VALUES (?, ?, ?)",
        ("123", "Miki", '{"language":"zh-TW"}'),
    )
    conn.execute(
        "INSERT INTO memories (user_id, keyword, content, importance) VALUES (?, ?, ?, ?)",
        ("123", "語言", "偏好繁體中文", 5),
    )
    conn.execute(
        "INSERT INTO vector_memories "
        "(user_id, keyword, content, embedding, importance) VALUES (?, ?, ?, ?, ?)",
        ("123", "喜好", "喜歡易讀報告", "[0.1, 0.2, 0.3]", 4),
    )
    conn.execute(
        "INSERT INTO messages (user_id, role, content, channel_id) VALUES (?, ?, ?, ?)",
        ("123", "user", "你好", "456"),
    )
    conn.commit()
    conn.close()

    snapshot = asyncio.run(get_user_memory_snapshot("123"))
    markdown = render_user_memory(snapshot)

    assert "# Miki 的 AI 記憶" in markdown
    assert "偏好繁體中文" in markdown
    assert "向量維度：3" in markdown
    assert "0.1" not in markdown
    assert "你好" in markdown


def test_table_snapshot_masks_vector_values(fresh_db) -> None:
    conn = sqlite3.connect(fresh_db)
    conn.execute(
        "INSERT INTO vector_memories "
        "(user_id, keyword, content, embedding, importance) VALUES (?, ?, ?, ?, ?)",
        ("123", "key", "content", "[0.1, 0.2]", 3),
    )
    conn.commit()
    conn.close()

    snapshot = asyncio.run(get_table_snapshot("vector_memories", 20))
    markdown = render_table_snapshot(snapshot)

    assert "<向量資料，2 維>" in markdown
    assert "[0.1, 0.2]" not in markdown


def test_table_snapshot_rejects_arbitrary_table_name(fresh_db) -> None:
    with pytest.raises(ValueError, match="不允許查詢"):
        asyncio.run(get_table_snapshot("sqlite_master", 20))
