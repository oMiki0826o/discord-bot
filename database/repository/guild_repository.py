"""
database/repository/guild_repository.py

職責：
- 伺服器（Guild）設定的純 SQL 查詢層
- 涵蓋：歡迎訊息、日誌頻道、自動身份組、工單設定
- 不含任何業務邏輯，只做資料存取

Modification():

- get_settings() / set_setting() / increment_ticket_count() /
  reset_settings() 套用 utils.async_db.to_thread 裝飾器：這些函式
  在 Bot 正常運作期間會被頻繁呼叫（幾乎每個管理指令、每則工單訊息
  的 log 發送都會查一次），原本是同步函式卻直接被 async 函式呼叫，
  等於每次呼叫都佔用事件迴圈直到 SQLite 查詢完成。
  set_setting() 與 increment_ticket_count() 內部原本會直接呼叫
  get_settings(guild_id) 確保該伺服器的列已存在，套用裝飾器後
  get_settings() 變成需要 await 的函式，這兩個函式也一併改為
  async def 並加上 await。呼叫端（mod.py／guild_settings.py／
  ticket.py）同步更新為 await。
  init_tables() 不套用：只在模組載入時執行一次，且已經整個被
  bot.py 的 `await asyncio.to_thread(initialize)` 包住執行。
- 整合自 Bot-Firefly 的 guild_service，改為 Repository Pattern
- 新增工單、日誌、歡迎、身份組等擴充欄位
- 使用 UPSERT，讀寫皆為獨立連線
- 新增 reset_settings()：原本 cogs/guild/guild_settings.py 的
  $server reset 直接在 Cog 內執行原始 SQL（DELETE FROM guild_settings），
  違反本檔自身註明的「不含任何業務邏輯，只做資料存取」原則，
  也讓資料表結構與欄位名稱分散在兩處維護。改為集中於此提供
  reset_settings()，Cog 端不再直接碰觸 SQL。

"""

from __future__ import annotations

import json

from database.ai.sqlite import get_connection
from utils.async_db import to_thread


# ── 初始化 ──────────────────────

def init_tables() -> None:
    """建立伺服器相關資料表。"""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id            INTEGER PRIMARY KEY,
            welcome_channel_id  INTEGER DEFAULT 0,
            leave_channel_id    INTEGER DEFAULT 0,
            log_channel_id      INTEGER DEFAULT 0,
            auto_role_id        INTEGER DEFAULT 0,
            ticket_category_id  INTEGER DEFAULT 0,
            ticket_support_role INTEGER DEFAULT 0,
            ticket_count        INTEGER DEFAULT 0,
            ai_enabled          INTEGER DEFAULT 1,
            extra               TEXT    DEFAULT '{}',
            updated_at          REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
    """)
    conn.commit()
    conn.close()


# ── 讀取 ──────────────────────

@to_thread
def get_settings(guild_id: int) -> dict:
    """
    取得伺服器設定，不存在時自動建立預設值並回傳。
    """
    conn = get_connection()
    c    = conn.cursor()
    c.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = c.fetchone()

    if not row:
        c.execute(
            "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)",
            (guild_id,),
        )
        conn.commit()
        c.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
        row = c.fetchone()

    conn.close()

    result = dict(row)
    try:
        result["extra"] = json.loads(result.get("extra") or "{}")
    except Exception:
        result["extra"] = {}
    return result


# ── 更新單一欄位 ──────────────────────

_ALLOWED_KEYS: frozenset[str] = frozenset({
    "welcome_channel_id",
    "leave_channel_id",
    "log_channel_id",
    "auto_role_id",
    "ticket_category_id",
    "ticket_support_role",
    "ai_enabled",
})


@to_thread
def _set_setting_sync(guild_id: int, key: str, value: int) -> None:
    """實際寫入邏輯（同步）；由 set_setting() 於確保列存在後呼叫。"""
    conn = get_connection()
    conn.execute(
        f"""
        UPDATE guild_settings
        SET {key} = ?, updated_at = unixepoch('now')
        WHERE guild_id = ?
        """,
        (value, guild_id),
    )
    conn.commit()
    conn.close()


async def set_setting(guild_id: int, key: str, value: int) -> None:
    """
    更新指定欄位。僅允許白名單中的欄位，防止 SQL 注入。
    不存在的 guild 會先建立預設列。
    """
    if key not in _ALLOWED_KEYS:
        raise ValueError(f"不允許的設定欄位: {key}")

    await get_settings(guild_id)   # 確保列存在
    await _set_setting_sync(guild_id, key, value)


# ── 工單計數 ──────────────────────

@to_thread
def _increment_ticket_count_sync(guild_id: int) -> int:
    """實際遞增邏輯（同步）；由 increment_ticket_count() 於確保列存在後呼叫。"""
    conn = get_connection()
    c    = conn.cursor()
    c.execute(
        """
        UPDATE guild_settings
        SET ticket_count = ticket_count + 1, updated_at = unixepoch('now')
        WHERE guild_id = ?
        """,
        (guild_id,),
    )
    conn.commit()
    c.execute("SELECT ticket_count FROM guild_settings WHERE guild_id = ?", (guild_id,))
    count = c.fetchone()["ticket_count"]
    conn.close()
    return count


async def increment_ticket_count(guild_id: int) -> int:
    """
    工單計數 +1，回傳更新後的計數值。
    用於生成唯一工單編號（如 ticket-0001）。
    """
    await get_settings(guild_id)   # 確保列存在
    return await _increment_ticket_count_sync(guild_id)


# ── 重置 ──────────────────────

@to_thread
def reset_settings(guild_id: int) -> None:
    """刪除指定伺服器的所有設定列，下次 get_settings() 會重新建立預設值。"""
    conn = get_connection()
    conn.execute("DELETE FROM guild_settings WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()


# ── 啟動時建立資料表 ──────────────────────

init_tables()
