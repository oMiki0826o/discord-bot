"""
database/repository/user_repository.py

Modification():
- 全部 19 個非 init_tables 函式套用 utils.async_db.to_thread 裝飾器：
  這是全專案 Repository 中函式數量最多的一個，涵蓋 tier／封鎖／
  暫時限制／互動計數／全域記憶／個人檔案／對話狀態，幾乎每次
  AI 對話與多個管理指令都會呼叫。原本全是同步函式卻直接被 async
  函式呼叫，改為透過 await 呼叫，實際執行委派給背景執行緒池。
  呼叫端遍布 core/ai（agent_router、context_manager、
  memory_manager、abuse_guard、user_context、admin_service）與
  多個 cogs，已逐一同步更新為 await。
  init_tables() 不套用：只在模組載入時執行一次，且已經整個被
  bot.py 的 `await asyncio.to_thread(initialize)` 包住執行。

職責：
- 使用者資料的純 SQL 查詢層（Repository Pattern）
- 涵蓋：等級、封鎖、互動計數、全域記憶、個人檔案、對話狀態
- 不含任何業務邏輯，只做資料存取

修正：
- 移除 social.json，所有使用者資料統一存入 SQLite
- 每個函式獨立連線開關，避免連線洩漏
- 移除未使用的 get_ban_reason() / list_bans()：全專案無呼叫端，
  $社交 指令已透過 user_context.dump_social() 取得封鎖名單與原因

新增（temp_restrictions，異常行為自動暫時限制）：
- 新增 temp_restrictions 表，記錄「系統自動偵測到短時間大量請求」時
  施加的暫時限制，與 user_bans（Owner 手動永久封鎖）區分：
    - user_bans：Owner 手動操作，需手動 $unban 才會解除
    - temp_restrictions：core.ai.abuse_guard 自動寫入，
      expires_at 過後自動失效，不需 Owner 介入
"""

from __future__ import annotations

import json

from database.ai.sqlite import get_connection
from utils.async_db import to_thread

# ── 初始化 ──────────────────────

def init_tables() -> None:
    """建立所有使用者相關資料表。"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_tiers (
            user_id    TEXT    PRIMARY KEY,
            tier       INTEGER NOT NULL DEFAULT 0,
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS user_bans (
            user_id    TEXT    PRIMARY KEY,
            reason     TEXT    DEFAULT '',
            created_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS user_interactions (
            user_id    TEXT    PRIMARY KEY,
            count      INTEGER NOT NULL DEFAULT 0,
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS global_memories (
            keyword    TEXT    PRIMARY KEY,
            content    TEXT    NOT NULL,
            importance INTEGER NOT NULL DEFAULT 5,
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id    TEXT    PRIMARY KEY,
            username   TEXT    NOT NULL DEFAULT '',
            data       TEXT    NOT NULL DEFAULT '{}',
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS conversation_states (
            user_id    TEXT    PRIMARY KEY,
            state      TEXT    NOT NULL DEFAULT 'normal',
            context    TEXT    NOT NULL DEFAULT '{}',
            expires_at REAL,
            updated_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS temp_restrictions (
            user_id    TEXT    PRIMARY KEY,
            reason     TEXT    NOT NULL DEFAULT '',
            expires_at REAL    NOT NULL,
            created_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
    """)
    conn.commit()
    conn.close()

# ── Tier ──────────────────────

@to_thread
def get_tier(user_id: str) -> int:
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT tier FROM user_tiers WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row["tier"] if row else 0


@to_thread
def set_tier(user_id: str, tier: int) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO user_tiers (user_id, tier)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            tier       = excluded.tier,
            updated_at = unixepoch('now')
        """,
        (user_id, tier),
    )
    conn.commit()
    conn.close()

# ── Ban ──────────────────────

@to_thread
def is_banned(user_id: str) -> bool:
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT 1 FROM user_bans WHERE user_id = ?", (user_id,))
    found = c.fetchone() is not None
    conn.close()
    return found


@to_thread
def ban(user_id: str, reason: str) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO user_bans (user_id, reason)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET reason = excluded.reason
        """,
        (user_id, reason),
    )
    conn.commit()
    conn.close()


@to_thread
def unban(user_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM user_bans WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ── 暫時限制（系統自動偵測異常行為） ──────────────────────

@to_thread
def get_temp_restriction(user_id: str) -> dict | None:
    """
    回傳暫時限制紀錄（不論是否已過期），呼叫方自行判斷 expires_at。
    回傳 None 表示無紀錄。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT reason, expires_at FROM temp_restrictions WHERE user_id = ?",
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {"reason": row["reason"], "expires_at": row["expires_at"]}


@to_thread
def set_temp_restriction(user_id: str, reason: str, expires_at: float) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO temp_restrictions (user_id, reason, expires_at)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            reason     = excluded.reason,
            expires_at = excluded.expires_at,
            created_at = unixepoch('now')
        """,
        (user_id, reason, expires_at),
    )
    conn.commit()
    conn.close()


@to_thread
def clear_temp_restriction(user_id: str) -> None:
    conn = get_connection()
    conn.execute("DELETE FROM temp_restrictions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

# ── Interactions ──────────────────────

@to_thread
def get_interaction_count(user_id: str) -> int:
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT count FROM user_interactions WHERE user_id = ?", (user_id,),
    )
    row = c.fetchone()
    conn.close()
    return row["count"] if row else 0


@to_thread
def increment_interaction(user_id: str) -> int:
    conn  = get_connection()
    c     = conn.cursor()
    c.execute(
        """
        INSERT INTO user_interactions (user_id, count)
        VALUES (?, 1)
        ON CONFLICT(user_id) DO UPDATE SET
            count      = count + 1,
            updated_at = unixepoch('now')
        """,
        (user_id,),
    )
    conn.commit()
    c.execute(
        "SELECT count FROM user_interactions WHERE user_id = ?", (user_id,),
    )
    count = c.fetchone()["count"]
    conn.close()
    return count

# ── Global Memories ──────────────────────

@to_thread
def list_global_memories() -> list[tuple[str, str, int]]:
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT keyword, content, importance FROM global_memories ORDER BY importance DESC",
    )
    rows = c.fetchall()
    conn.close()
    return [(r["keyword"], r["content"], r["importance"]) for r in rows]


@to_thread
def upsert_global_memory(keyword: str, content: str, importance: int) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO global_memories (keyword, content, importance)
        VALUES (?, ?, ?)
        ON CONFLICT(keyword) DO UPDATE SET
            content    = excluded.content,
            importance = excluded.importance,
            updated_at = unixepoch('now')
        """,
        (keyword, content, importance),
    )
    conn.commit()
    conn.close()


@to_thread
def delete_global_memory(keyword: str) -> bool:
    conn = get_connection()
    c    = conn.cursor()
    c.execute("DELETE FROM global_memories WHERE keyword = ?", (keyword,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

# ── Profile ──────────────────────

@to_thread
def get_profile(user_id: str) -> dict:
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT data FROM user_profiles WHERE user_id = ?", (user_id,),
    )
    row = c.fetchone()
    conn.close()
    try:
        return json.loads(row["data"]) if row else {}
    except Exception:
        return {}


@to_thread
def save_profile(user_id: str, username: str, data: dict) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO user_profiles (user_id, username, data)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username   = excluded.username,
            data       = excluded.data,
            updated_at = unixepoch('now')
        """,
        (user_id, username, json.dumps(data, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()

# ── State ──────────────────────

@to_thread
def get_state_row(user_id: str) -> dict | None:
    """
    回傳原始 state 列，呼叫方負責判斷是否過期。
    回傳 None 表示無紀錄。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT state, context, expires_at FROM conversation_states
        WHERE user_id = ?
        """,
        (user_id,),
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "state":      row["state"],
        "context":    json.loads(row["context"] or "{}"),
        "expires_at": row["expires_at"],
    }


@to_thread
def upsert_state(
    user_id:    str,
    state:      str,
    context:    dict,
    expires_at: float | None,
) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO conversation_states (user_id, state, context, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            state      = excluded.state,
            context    = excluded.context,
            expires_at = excluded.expires_at,
            updated_at = unixepoch('now')
        """,
        (user_id, state, json.dumps(context, ensure_ascii=False), expires_at),
    )
    conn.commit()
    conn.close()


@to_thread
def delete_state(user_id: str) -> None:
    conn = get_connection()
    conn.execute(
        "DELETE FROM conversation_states WHERE user_id = ?", (user_id,),
    )
    conn.commit()
    conn.close()


@to_thread
def list_active_states(now: float) -> list[dict]:
    """取得所有非 normal 且未過期的對話狀態。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT user_id, state, expires_at
        FROM   conversation_states
        WHERE  state != 'normal'
          AND  (expires_at IS NULL OR expires_at > ?)
        ORDER  BY updated_at DESC
        """,
        (now,),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "user_id":    r["user_id"],
            "state":      r["state"],
            "expires_at": r["expires_at"],
        }
        for r in rows
    ]


# ── 啟動時建立資料表 ──────────────────────
init_tables()
