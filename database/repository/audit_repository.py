"""
database/repository/audit_repository.py

職責：
- 管理指令操作紀錄（audit log）的純 SQL 查詢層
- 不含業務邏輯，只做存取

設計說明：
- 獨立於 user_repository.py / memory_repository.py，因為 audit log
  記錄的是「管理動作」本身，而非使用者或記憶資料；
  涵蓋的目標也不限於使用者（例如 $dashboard del 操作的是 Prompt 模板）
- 寫入由 core.ai.admin_service 透過 event_bus 的 "admin_action" 事件
  觸發（詳見該檔說明），呼叫端（cogs）不直接 import 本檔
"""

from __future__ import annotations

from database.ai.sqlite import get_connection

# ── 初始化 ────────────────────────────────────────────────────────────

def init_tables() -> None:
    """建立 audit_log 資料表。"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id   TEXT    NOT NULL,
            command    TEXT    NOT NULL,
            target_id  TEXT    NOT NULL DEFAULT '',
            detail     TEXT    NOT NULL DEFAULT '',
            created_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_audit_time
            ON audit_log(created_at DESC);
    """)
    conn.commit()
    conn.close()

# ── 寫入 ──────────────────────────────────────────────────────────────

def insert_log(
    actor_id:  str,
    command:   str,
    target_id: str = "",
    detail:    str = "",
) -> None:
    """
    新增一筆操作紀錄。

    actor_id：執行指令的人（通常是 Owner）
    command：指令名稱，例如 "tier" / "ban" / "dashboard.del"
    target_id：被操作的對象 ID（使用者 / 模板名稱等），無對象時留空
    detail：補充說明，例如 ban 的原因、tier 的數值
    """
    conn = get_connection()
    conn.execute(
        "INSERT INTO audit_log (actor_id, command, target_id, detail) "
        "VALUES (?, ?, ?, ?)",
        (actor_id, command, target_id, detail),
    )
    conn.commit()
    conn.close()

# ── 查詢 ──────────────────────────────────────────────────────────────

def get_recent(limit: int = 20) -> list[dict]:
    """取得最近 N 筆操作紀錄，依時間降序。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        "SELECT actor_id, command, target_id, detail, created_at "
        "FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = c.fetchall()
    conn.close()
    return [
        {
            "actor_id":   r["actor_id"],
            "command":    r["command"],
            "target_id":  r["target_id"],
            "detail":     r["detail"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ── 啟動時建立資料表 ──────────────────────────────────────────────────
init_tables()
