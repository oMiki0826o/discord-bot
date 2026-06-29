"""
core/ai/search_manager.py

Modification():
- TTL 與模糊命中閾值改為使用時讀取 settings.json，符合熱更新語意。
- 設定值加入範圍保護，避免 0、負數或超過 1 的 fuzzy threshold 造成異常行為。
- 保留精確命中優先、模糊命中其次的搜尋快取策略。

Description():

- 本檔管理 Grounding 搜尋結果快取，命中時可跳過外部搜尋以節省 Token。
- check_cache() 回傳 (快取結果, 是否仍需搜尋)，save_result() 負責回填快取。

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
- 短期 TTL、長期 TTL 與模糊比對閾值由 settings.json 統一提供，可熱更新。
"""

from __future__ import annotations

import hashlib
import logging
import time
from difflib import SequenceMatcher

from core.system.settings import get_float, get_int
from database.ai.sqlite import get_connection

logger = logging.getLogger("bot.search_manager")

# ── 常數 ──────────────────────

_SHORT_TTL_KEYWORDS = (
    "天氣", "股價", "匯率", "即時", "最新", "現在", "今天",
    "weather", "stock", "price", "latest", "current",
)

# ── DB 初始化 ──────────────────────

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

# ── 工具 ──────────────────────

def _hash(query: str) -> str:
    return hashlib.md5(query.lower().strip().encode()).hexdigest()


def _short_ttl_min() -> int:
    """取得時效性查詢快取分鐘數。"""
    return max(1, get_int("ai.search_short_ttl_min", 30))


def _long_ttl_min() -> int:
    """取得一般查詢快取分鐘數。"""
    return max(1, get_int("ai.search_long_ttl_min", 1440))


def _fuzzy_threshold() -> float:
    """取得模糊命中門檻，限制在 0.0 到 1.0。"""
    return min(1.0, max(0.0, get_float("ai.search_fuzzy_threshold", 0.85)))


def _ttl(query: str) -> int:
    q = query.lower()
    return _short_ttl_min() if any(k in q for k in _SHORT_TTL_KEYWORDS) else _long_ttl_min()

# ── 快取 ──────────────────────

def check_cache(query: str) -> tuple[str | None, bool]:
    """
    回傳 (快取結果 | None, 是否仍需 Grounding)。
    命中 → (result, False)
    未命中 → (None, True)
    """
    now  = time.time()
    conn = get_connection()
    c    = conn.cursor()

    # ── 精確命中 ──────────────────────
    c.execute(
        "SELECT result FROM search_cache WHERE query_hash=? AND expires_at>?",
        (_hash(query), now),
    )
    row = c.fetchone()
    if row:
        conn.close()
        logger.debug("[search_manager] cache exact: %r", query[:50])
        return row["result"], False

    # ── 模糊命中 ──────────────────────
    c.execute(
        "SELECT query_text, result FROM search_cache WHERE expires_at>?", (now,),
    )
    rows    = c.fetchall()
    conn.close()
    q_lower = query.lower()
    fuzzy_threshold = _fuzzy_threshold()
    for r in rows:
        ratio = SequenceMatcher(None, q_lower, r["query_text"].lower()).ratio()
        if ratio >= fuzzy_threshold:
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
