"""
database/repository/favorites_repository.py

職責：
- 使用者音樂收藏清單的 SQLite 資料存取層
- 支援新增、刪除、查詢、清空

Modification():

- add_favorite() / remove_favorite() / clear_favorites() /
  get_favorites() / is_favorite() 套用 utils.async_db.to_thread
  裝飾器：這些函式會在 Bot 正常運作期間被重複呼叫（每次 /fav
  相關指令），原本是同步函式卻直接被 async 函式呼叫，等於每次
  呼叫都會佔用事件迴圈直到 SQLite 查詢完成。呼叫端
  （cogs/utility/favorites.py）同步更新為 await；其中
  remove_favorite_core() 與 _get_favorite_at() 兩個原本是同步的
  輔助函式，因為改為呼叫這裡新增的 async 函式，也一併改為 async，
  並更新它們自己的呼叫端。
  init_tables() 不套用：只在模組載入時執行一次，且已經整個被
  bot.py 的 `await asyncio.to_thread(initialize)` 包住執行。
- 移植自 Bot-Firefly core/music/favorites.py（原為 JSON 檔案）
- 改用 SQLite，保持與其他 Repository 一致的設計模式

"""

from __future__ import annotations

from database.ai.sqlite import get_connection
from utils.async_db import to_thread


# ── 初始化 ──────────────────────

def init_tables() -> None:
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS music_favorites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT    NOT NULL,
            title       TEXT    NOT NULL,
            url         TEXT    NOT NULL,
            duration    INTEGER DEFAULT 0,
            added_at    REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_fav_user
            ON music_favorites(user_id, added_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fav_unique
            ON music_favorites(user_id, url);
    """)
    conn.commit()
    conn.close()


# ── 新增 ──────────────────────

@to_thread
def add_favorite(user_id: str, title: str, url: str, duration: int = 0) -> bool:
    """新增收藏，已存在時回傳 False。"""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR IGNORE INTO music_favorites (user_id, title, url, duration) VALUES (?,?,?,?)",
            (user_id, title, url, duration),
        )
        conn.commit()
        changed = conn.execute("SELECT changes()").fetchone()[0] > 0
        conn.close()
        return changed
    except Exception:
        return False


# ── 刪除 ──────────────────────

@to_thread
def remove_favorite(user_id: str, url: str) -> bool:
    conn = get_connection()
    conn.execute(
        "DELETE FROM music_favorites WHERE user_id = ? AND url = ?",
        (user_id, url),
    )
    conn.commit()
    changed = conn.execute("SELECT changes()").fetchone()[0] > 0
    conn.close()
    return changed


@to_thread
def clear_favorites(user_id: str) -> int:
    conn = get_connection()
    conn.execute("DELETE FROM music_favorites WHERE user_id = ?", (user_id,))
    conn.commit()
    count = conn.execute("SELECT changes()").fetchone()[0]
    conn.close()
    return count


# ── 查詢 ──────────────────────

@to_thread
def get_favorites(user_id: str) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT title, url, duration, added_at FROM music_favorites WHERE user_id = ? ORDER BY added_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@to_thread
def is_favorite(user_id: str, url: str) -> bool:
    conn = get_connection()
    row  = conn.execute(
        "SELECT 1 FROM music_favorites WHERE user_id = ? AND url = ?",
        (user_id, url),
    ).fetchone()
    conn.close()
    return row is not None


init_tables()
