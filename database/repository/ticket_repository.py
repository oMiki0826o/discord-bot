"""
database/repository/ticket_repository.py

職責：
- 工單資料的純 SQL 查詢層
- 涵蓋：工單建立、狀態更新、查詢、關閉紀錄
- 不含任何業務邏輯，只做資料存取

Modification():

- 全新建立，對應 cogs/ticket/ticket.py 的資料需求
- 使用 UPSERT 避免重複插入
- 工單狀態：open / closed

"""

from __future__ import annotations

from database.ai.sqlite import get_connection


# ── 初始化 ──────────────────────

def init_tables() -> None:
    """建立工單相關資料表。"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            ticket_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     INTEGER NOT NULL,
            channel_id   INTEGER NOT NULL UNIQUE,
            user_id      TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'open',
            topic        TEXT    NOT NULL DEFAULT '',
            closed_by    TEXT,
            created_at   REAL    NOT NULL DEFAULT (unixepoch('now')),
            closed_at    REAL
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_guild
            ON tickets(guild_id, status);
        CREATE INDEX IF NOT EXISTS idx_tickets_user
            ON tickets(user_id, status);
    """)
    conn.commit()
    conn.close()


# ── 寫入 ──────────────────────

def create_ticket(
    guild_id:   int,
    channel_id: int,
    user_id:    str,
    topic:      str = "",
) -> int:
    """
    建立工單紀錄，回傳 ticket_id。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        INSERT INTO tickets (guild_id, channel_id, user_id, topic)
        VALUES (?, ?, ?, ?)
        """,
        (guild_id, channel_id, user_id, topic),
    )
    conn.commit()
    ticket_id = c.lastrowid
    conn.close()
    return ticket_id


def close_ticket(channel_id: int, closed_by: str) -> bool:
    """
    將指定頻道的工單標記為 closed。
    回傳 True 表示成功關閉，False 表示找不到工單。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        UPDATE tickets
        SET status = 'closed', closed_by = ?, closed_at = unixepoch('now')
        WHERE channel_id = ? AND status = 'open'
        """,
        (closed_by, channel_id),
    )
    conn.commit()
    updated = c.rowcount > 0
    conn.close()
    return updated


# ── 查詢 ──────────────────────

def get_ticket_by_channel(channel_id: int) -> dict | None:
    """依頻道 ID 查詢工單，找不到回傳 None。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT * FROM tickets WHERE channel_id = ?",
        (channel_id,),
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_open_tickets_by_user(guild_id: int, user_id: str) -> list[dict]:
    """取得指定使用者在伺服器中所有尚未關閉的工單。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT * FROM tickets
        WHERE guild_id = ? AND user_id = ? AND status = 'open'
        ORDER BY created_at DESC
        """,
        (guild_id, user_id),
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_guild_stats(guild_id: int) -> dict:
    """取得伺服器工單統計資料，供 $ticket stats 使用。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'open'   THEN 1 ELSE 0 END) AS open_count,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_count
        FROM tickets
        WHERE guild_id = ?
        """,
        (guild_id,),
    )
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {"total": 0, "open_count": 0, "closed_count": 0}


# ── 啟動時建立資料表 ──────────────────────

init_tables()
