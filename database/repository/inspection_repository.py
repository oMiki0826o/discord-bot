"""Owner 專用的唯讀資料庫檢視層。

所有動態表名都必須先通過白名單，不接受任意 SQL。
"""

from __future__ import annotations

import json

from database.ai.sqlite import get_connection
from utils.async_db import to_thread


TABLE_LABELS: dict[str, str] = {
    "audit_log": "管理操作稽核",
    "channel_summaries": "頻道摘要",
    "conversation_states": "對話狀態",
    "error_log": "AI 錯誤",
    "global_memories": "全域記憶",
    "guild_settings": "伺服器設定",
    "guild_vc_settings": "語音設定",
    "memories": "一般記憶",
    "messages": "AI 對話",
    "mod_log": "管理紀錄",
    "music_favorites": "音樂收藏",
    "role_panels": "身分組面板",
    "search_cache": "搜尋快取",
    "summaries": "使用者摘要",
    "temp_restrictions": "暫時限制",
    "temp_voice_channels": "臨時語音頻道",
    "tickets": "Ticket",
    "token_budget": "Token 紀錄",
    "user_bans": "使用者封鎖",
    "user_interactions": "互動統計",
    "user_profiles": "使用者資料",
    "user_tiers": "使用等級",
    "vector_memories": "向量記憶",
    "warn_log": "警告紀錄",
}

_ALLOWED_TABLES = frozenset(TABLE_LABELS)
_MAX_PREVIEW_ROWS = 100


def _require_table(table: str) -> str:
    normalized = table.strip().lower()
    if normalized not in _ALLOWED_TABLES:
        raise ValueError(f"不允許查詢的資料表: {table}")
    return normalized


@to_thread
def get_database_overview() -> list[dict]:
    """取得白名單內已建立資料表的筆數。"""
    conn = get_connection()
    try:
        existing = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        result: list[dict] = []
        for table, label in TABLE_LABELS.items():
            if table not in existing:
                continue
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            result.append({"table": table, "label": label, "rows": count})
        return result
    finally:
        conn.close()


@to_thread
def get_table_snapshot(table: str, limit: int = 20) -> dict:
    """取得指定資料表的最新資料，最多 100 筆。"""
    table = _require_table(table)
    limit = max(1, min(int(limit), _MAX_PREVIEW_ROWS))
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if not exists:
            raise ValueError(f"資料表尚未建立: {table}")

        columns = [
            row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')
        ]
        total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        order_column = next(
            (name for name in ("id", "ticket_id", "panel_id", "created_at", "updated_at")
             if name in columns),
            None,
        )
        order_sql = f' ORDER BY "{order_column}" DESC' if order_column else ""
        rows = conn.execute(
            f'SELECT * FROM "{table}"{order_sql} LIMIT ?', (limit,)
        ).fetchall()
        return {
            "table": table,
            "label": TABLE_LABELS[table],
            "columns": columns,
            "total": total,
            "limit": limit,
            "rows": [dict(row) for row in rows],
        }
    finally:
        conn.close()


@to_thread
def get_user_memory_snapshot(user_id: str) -> dict:
    """整合單一使用者的 AI 記憶與對話資料。"""
    conn = get_connection()
    try:
        profile_row = conn.execute(
            "SELECT username, data, updated_at FROM user_profiles WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        summary_row = conn.execute(
            "SELECT summary, msg_count, updated_at FROM summaries WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        channel_summaries = conn.execute(
            "SELECT channel_id, summary, msg_count, updated_at "
            "FROM channel_summaries WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        memories = conn.execute(
            "SELECT id, scope_type, channel_id, keyword, category, subject, content, "
            "importance, confidence, status, source_message_id, source_excerpt, "
            "created_at, updated_at FROM memories "
            "WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        vectors = conn.execute(
            "SELECT memory_id, channel_id, keyword, content, embedding_model, "
            "embedding, importance, created_at, updated_at "
            "FROM vector_memories WHERE user_id = ? "
            "ORDER BY importance DESC, created_at DESC",
            (user_id,),
        ).fetchall()
        messages = conn.execute(
            "SELECT role, content, channel_id, created_at FROM messages "
            "WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        ).fetchall()
        global_memories = conn.execute(
            "SELECT keyword, content, importance, updated_at FROM global_memories "
            "ORDER BY importance DESC, keyword ASC"
        ).fetchall()

        profile: dict = {}
        username = ""
        profile_updated_at = None
        if profile_row:
            username = profile_row["username"]
            profile_updated_at = profile_row["updated_at"]
            try:
                decoded = json.loads(profile_row["data"] or "{}")
                profile = decoded if isinstance(decoded, dict) else {"value": decoded}
            except (TypeError, json.JSONDecodeError):
                profile = {"raw": profile_row["data"]}

        vector_rows: list[dict] = []
        for row in vectors:
            item = dict(row)
            raw_embedding = item.pop("embedding", "")
            try:
                embedding = json.loads(raw_embedding)
                item["embedding_dimensions"] = len(embedding) if isinstance(embedding, list) else 0
            except (TypeError, json.JSONDecodeError):
                item["embedding_dimensions"] = 0
            vector_rows.append(item)

        return {
            "user_id": user_id,
            "username": username,
            "profile": profile,
            "profile_updated_at": profile_updated_at,
            "summary": dict(summary_row) if summary_row else None,
            "channel_summaries": [dict(row) for row in channel_summaries],
            "memories": [dict(row) for row in memories],
            "vector_memories": vector_rows,
            "messages": [dict(row) for row in messages],
            "global_memories": [dict(row) for row in global_memories],
        }
    finally:
        conn.close()
