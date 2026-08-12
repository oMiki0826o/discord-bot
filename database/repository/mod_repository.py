"""
database/repository/mod_repository.py

職責：
- 管理（Moderation）資料的純 SQL 查詢層
- 涵蓋：警告紀錄、禁言紀錄、踢出/封禁紀錄
- 不含任何業務邏輯，只做資料存取

Modification():

- add_warn() / get_warnings() / count_warnings() / clear_warnings() /
  log_action() / get_mod_log() 套用 utils.async_db.to_thread 裝飾器：
  這些函式在管理指令執行時會被呼叫，原本是同步函式卻直接被 async
  函式呼叫，改為透過 await 呼叫，實際執行委派給背景執行緒池，不
  阻塞事件迴圈。呼叫端（cogs/moderation/mod.py）同步更新為 await。
  init_tables() 不套用：只在模組載入時執行一次，且已經整個被
  bot.py 的 `await asyncio.to_thread(initialize)` 包住執行。
- 全新建立，對應 cogs/moderation/mod.py 的資料需求
- warn_log 紀錄每一次警告（可累計檢視違規歷史）
- 所有操作均記錄操作者（moderator_id）

"""

from __future__ import annotations

from database.ai.sqlite import get_connection
from utils.async_db import to_thread


# ── 初始化 ──────────────────────

def init_tables() -> None:
    """建立管理相關資料表。"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS warn_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id      INTEGER NOT NULL,
            user_id       TEXT    NOT NULL,
            moderator_id  TEXT    NOT NULL,
            reason        TEXT    NOT NULL DEFAULT '',
            created_at    REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_warn_user
            ON warn_log(guild_id, user_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS mod_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id      INTEGER NOT NULL,
            action        TEXT    NOT NULL,
            user_id       TEXT    NOT NULL,
            moderator_id  TEXT    NOT NULL,
            reason        TEXT    NOT NULL DEFAULT '',
            duration_min  INTEGER,
            created_at    REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_modlog_guild
            ON mod_log(guild_id, created_at DESC);
    """)
    conn.commit()
    conn.close()


# ── 警告 ──────────────────────

@to_thread
def add_warn(
    guild_id:     int,
    user_id:      str,
    moderator_id: str,
    reason:       str = "",
) -> int:
    """
    新增警告紀錄，回傳該使用者在此伺服器的累計警告次數。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        INSERT INTO warn_log (guild_id, user_id, moderator_id, reason)
        VALUES (?, ?, ?, ?)
        """,
        (guild_id, user_id, moderator_id, reason),
    )
    conn.commit()
    c.execute(
        "SELECT COUNT(*) AS cnt FROM warn_log WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    count = c.fetchone()["cnt"]
    conn.close()
    return count


@to_thread
def get_warnings(guild_id: int, user_id: str, limit: int = 10) -> list[dict]:
    """取得指定使用者的警告紀錄（最新在前）。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT id, moderator_id, reason, created_at
        FROM warn_log
        WHERE guild_id = ? AND user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (guild_id, user_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@to_thread
def count_warnings(guild_id: int, user_id: str) -> int:
    """回傳使用者在伺服器的累計警告次數。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT COUNT(*) AS cnt FROM warn_log WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    count = c.fetchone()["cnt"]
    conn.close()
    return count


@to_thread
def clear_warnings(guild_id: int, user_id: str) -> int:
    """清除使用者所有警告，回傳被清除的筆數。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "DELETE FROM warn_log WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    conn.commit()
    deleted = c.rowcount
    conn.close()
    return deleted


# ── 管理動作記錄 ──────────────────────

@to_thread
def log_action(
    guild_id:     int,
    action:       str,
    user_id:      str,
    moderator_id: str,
    reason:       str = "",
    duration_min: int | None = None,
) -> None:
    """
    記錄管理動作（kick / ban / mute / unmute 等）。
    action 為動作字串，duration_min 僅 mute 時使用。
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO mod_log (guild_id, action, user_id, moderator_id, reason, duration_min)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (guild_id, action, user_id, moderator_id, reason, duration_min),
    )
    conn.commit()
    conn.close()


@to_thread
def get_mod_log(guild_id: int, limit: int = 20) -> list[dict]:
    """取得伺服器最近的管理動作紀錄（最新在前）。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT action, user_id, moderator_id, reason, duration_min, created_at
        FROM mod_log
        WHERE guild_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (guild_id, limit),
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── 啟動時建立資料表 ──────────────────────

init_tables()
