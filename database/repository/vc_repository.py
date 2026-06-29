"""
database/repository/vc_repository.py

職責：
- 臨時語音頻道（Temporary Voice Channel）的資料存取層
- 記錄每個動態建立的頻道：頻道 ID、擁有者、設定（名稱/人數/狀態）
- Bot 重啟後可憑此判斷哪些頻道屬於動態頻道，並清理已空的頻道

Modification():

- 全新建立，對應 cogs/voice/voice_channel.py 的資料需求
- guild_vc_settings 儲存每個伺服器的 JTC 設定（觸發頻道、類別、範本）
- temp_voice_channels 記錄所有現存的臨時頻道

"""

from __future__ import annotations

import json

from database.ai.sqlite import get_connection


# ── 初始化 ──────────────────────

def init_tables() -> None:
    """建立語音頻道相關資料表。"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS guild_vc_settings (
            guild_id        INTEGER PRIMARY KEY,
            create_channel  INTEGER DEFAULT 0,
            category_id     INTEGER DEFAULT 0,
            name_template   TEXT    DEFAULT '{username} 的頻道',
            default_limit   INTEGER DEFAULT 0,
            updated_at      REAL    NOT NULL DEFAULT (unixepoch('now'))
        );

        CREATE TABLE IF NOT EXISTS temp_voice_channels (
            channel_id  INTEGER PRIMARY KEY,
            guild_id    INTEGER NOT NULL,
            owner_id    TEXT    NOT NULL,
            name        TEXT    NOT NULL DEFAULT '',
            user_limit  INTEGER NOT NULL DEFAULT 0,
            is_locked   INTEGER NOT NULL DEFAULT 0,
            created_at  REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_tvc_guild
            ON temp_voice_channels(guild_id);
    """)
    conn.commit()
    conn.close()


# ── 伺服器 JTC 設定 ──────────────────────

def get_vc_settings(guild_id: int) -> dict:
    """
    取得伺服器的 JTC 設定。
    不存在時回傳含預設值的 dict（不自動寫入 DB）。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT * FROM guild_vc_settings WHERE guild_id = ?", (guild_id,))
    row = c.fetchone()
    conn.close()

    if row:
        return dict(row)
    return {
        "guild_id":       guild_id,
        "create_channel": 0,
        "category_id":    0,
        "name_template":  "{username} 的頻道",
        "default_limit":  0,
    }


def set_vc_setting(guild_id: int, key: str, value) -> None:
    """更新 JTC 設定的單一欄位。"""
    _ALLOWED = frozenset({
        "create_channel", "category_id", "name_template", "default_limit",
    })
    if key not in _ALLOWED:
        raise ValueError(f"不允許的設定欄位: {key}")

    conn = get_connection()
    conn.execute(
        f"""
        INSERT INTO guild_vc_settings (guild_id, {key})
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            {key}      = excluded.{key},
            updated_at = unixepoch('now')
        """,
        (guild_id, value),
    )
    conn.commit()
    conn.close()


# ── 臨時語音頻道 ──────────────────────

def create_channel(
    channel_id: int,
    guild_id:   int,
    owner_id:   str,
    name:       str,
    user_limit: int = 0,
) -> None:
    """記錄新建立的臨時語音頻道。"""
    conn = get_connection()
    conn.execute(
        """
        INSERT OR REPLACE INTO temp_voice_channels
            (channel_id, guild_id, owner_id, name, user_limit)
        VALUES (?, ?, ?, ?, ?)
        """,
        (channel_id, guild_id, owner_id, name, user_limit),
    )
    conn.commit()
    conn.close()


def delete_channel(channel_id: int) -> None:
    """從資料庫移除臨時語音頻道紀錄（頻道刪除後呼叫）。"""
    conn = get_connection()
    conn.execute(
        "DELETE FROM temp_voice_channels WHERE channel_id = ?",
        (channel_id,),
    )
    conn.commit()
    conn.close()


def get_channel(channel_id: int) -> dict | None:
    """依頻道 ID 查詢臨時頻道資料，找不到回傳 None。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT * FROM temp_voice_channels WHERE channel_id = ?",
        (channel_id,),
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_channels(guild_id: int) -> list[dict]:
    """取得伺服器所有現存的臨時語音頻道（用於重啟後清理）。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT * FROM temp_voice_channels WHERE guild_id = ?",
        (guild_id,),
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_channel(channel_id: int, key: str, value) -> None:
    """更新臨時頻道的單一屬性。"""
    _ALLOWED = frozenset({"owner_id", "name", "user_limit", "is_locked"})
    if key not in _ALLOWED:
        raise ValueError(f"不允許的欄位: {key}")

    conn = get_connection()
    conn.execute(
        f"""
        UPDATE temp_voice_channels
        SET {key} = ?
        WHERE channel_id = ?
        """,
        (value, channel_id),
    )
    conn.commit()
    conn.close()


def is_temp_channel(channel_id: int) -> bool:
    """快速判斷頻道是否為本系統管理的臨時頻道。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT 1 FROM temp_voice_channels WHERE channel_id = ?",
        (channel_id,),
    )
    found = c.fetchone() is not None
    conn.close()
    return found


# ── 啟動時建立資料表 ──────────────────────

init_tables()
