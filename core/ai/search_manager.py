"""
core/ai/search_manager.py

職責：
- 搜尋快取：命中時跳過 Grounding，節省 Token
- 統一入口：check_cache() 回傳 (快取結果, 是否仍需搜尋)

合併來源：
- core/ai/search_cache.py
- core/ai/web_search.py

修正：
- 精確命中（MD5）優先，再做 SequenceMatcher 模糊命中（閾值 0.85）
- TTL：時效性關鍵字 30 分鐘，一般查詢 24 小時
- save_result() 供 core.py 在 Grounding 完成後回填快取
- 移除未使用的 extract_urls() / fetch_url()：原為「使用者貼上 URL 時
  抓取網頁內容」的功能，但全專案無任何呼叫端，連帶移除 httpx 依賴
  與僅供其使用的 _URL_RE / _URL_TIMEOUT / _MAX_CONTENT / _BLOCKED_HOSTS
  常數。如未來需要此功能，建議獨立為 core.ai.url_fetcher 並於
  agent_router 加入對應路由規則後再啟用

新增（行為調校集中化）：
- _SHORT_TTL_MIN / _LONG_TTL_MIN / _FUZZY_THR 改由 config.py
  統一提供，可透過環境變數調整（預設值與優化前相同）
"""

from __future__ import annotations

import hashlib
import logging
import time
from difflib import SequenceMatcher

from core.system.settings import get as _s
from database.ai.sqlite import get_connection

logger = logging.getLogger("bot.search_manager")

# ── 常數 ──────────────────────────────────────────────────────────────

_SHORT_TTL_KEYWORDS = (
    "天氣", "股價", "匯率", "即時", "最新", "現在", "今天",
    "weather", "stock", "price", "latest", "current",
)
_SHORT_TTL_MIN  = int(_s('ai.search_short_ttl_min', 30))
_LONG_TTL_MIN   = int(_s('ai.search_long_ttl_min', 1440))
_FUZZY_THR      = float(_s('ai.search_fuzzy_threshold', 0.85))

# ── DB 初始化 ──────────────────────────────────────────────────────────

def _init() -> None:
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS search_cache (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            query_hash TEXT    NOT NULL UNIQUE,
            query_text TEXT    NOT NULL,
            result     TEXT    NOT NULL,
            expires_at REAL    NOT NULL,
            created_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_cache_hash   ON search_cache(query_hash);
        CREATE INDEX IF NOT EXISTS idx_cache_expire ON search_cache(expires_at);
    """)
    conn.commit()
    conn.close()


_init()

# ── 工具 ──────────────────────────────────────────────────────────────

def _hash(query: str) -> str:
    return hashlib.md5(query.lower().strip().encode()).hexdigest()


def _ttl(query: str) -> int:
    q = query.lower()
    return _SHORT_TTL_MIN if any(k in q for k in _SHORT_TTL_KEYWORDS) else _LONG_TTL_MIN

# ── 快取 ──────────────────────────────────────────────────────────────

def check_cache(query: str) -> tuple[str | None, bool]:
    """
    回傳 (快取結果 | None, 是否仍需 Grounding)。
    命中 → (result, False)
    未命中 → (None, True)
    """
    now  = time.time()
    conn = get_connection()
    c    = conn.cursor()

    # ── 精確命中 ───────────────────────────────────────────────
    c.execute(
        "SELECT result FROM search_cache WHERE query_hash=? AND expires_at>?",
        (_hash(query), now),
    )
    row = c.fetchone()
    if row:
        conn.close()
        logger.debug("[search_manager] cache exact: %r", query[:50])
        return row["result"], False

    # ── 模糊命中 ───────────────────────────────────────────────
    c.execute(
        "SELECT query_text, result FROM search_cache WHERE expires_at>?", (now,),
    )
    rows    = c.fetchall()
    conn.close()
    q_lower = query.lower()
    for r in rows:
        ratio = SequenceMatcher(None, q_lower, r["query_text"].lower()).ratio()
        if ratio >= _FUZZY_THR:
            logger.debug(
                "[search_manager] cache fuzzy ratio=%.2f: %r", ratio, query[:50],
            )
            return r["result"], False

    return None, True


def save_result(query: str, result: str) -> None:
    """Grounding 完成後回填快取。空結果不存。"""
    if not result or not result.strip():
        return
    expires_at = time.time() + _ttl(query) * 60
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO search_cache (query_hash, query_text, result, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(query_hash) DO UPDATE SET
                result=excluded.result, expires_at=excluded.expires_at,
                created_at=unixepoch('now')
            """,
            (_hash(query), query.lower().strip(), result, expires_at),
        )
        conn.commit()
        conn.close()
        logger.debug("[search_manager] saved ttl=%dm: %r", _ttl(query), query[:50])
    except Exception as e:
        logger.debug("[search_manager] save error: %s", e)


def cleanup_expired() -> int:
    """刪除過期快取，回傳刪除筆數。"""
    try:
        conn = get_connection()
        c    = conn.cursor()
        c.execute("DELETE FROM search_cache WHERE expires_at<=?", (time.time(),))
        n    = c.rowcount
        conn.commit()
        conn.close()
        if n:
            logger.info("[search_manager] cleanup removed=%d", n)
        return n
    except Exception as e:
        logger.debug("[search_manager] cleanup error: %s", e)
        return 0


def get_stats() -> dict:
    try:
        conn  = get_connection()
        c     = conn.cursor()
        c.execute("SELECT COUNT(*) FROM search_cache")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM search_cache WHERE expires_at>?", (time.time(),))
        valid = c.fetchone()[0]
        conn.close()
        return {"total": total, "valid": valid, "expired": total - valid}
    except Exception:
        return {"total": 0, "valid": 0, "expired": 0}
