"""
core/ai/budget.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

職責：
- 記錄每次 API 呼叫的 Token 用量（實際值或估算值）
- 記錄錯誤事件
- 提供統計查詢供 $info / $dashboard 使用

修正（Stage 5 架構重構）：
- 由 core/budget.py 移至 core/ai/budget.py
- get_global_stats() 的 active_users 改為獨立查詢精確計算
  （原本以「模型數量」近似，數值不正確）
- 移除查詢中未使用的 COUNT(DISTINCT user_id) AS users 欄位
- 新增 get_top_users()：原本由 cogs/ai/dashboard.py 直接以
  database.ai.sqlite.get_connection() 查詢 token_budget 排行，
  違反「Cog 不直接操作 SQLite」的分層原則，現集中於此並透過
  admin_service 暴露給 dashboard.py 呼叫

設計說明：
- Gemini API 回傳 usage_metadata 時使用實際值，否則以字元數估算
- 估算公式：中英文混合約 3 字元 / token（粗估，用於趨勢監控而非計費；
  原註解誤寫為 2.5 字元 / token，與 _estimate() 實際的 len(text)//3
  不一致，已更正說明以符合實際公式）
- 所有寫入皆非同步包裝，不阻塞主流程

新增（用量精確度透明化）：
- get_global_stats() 新增 estimated_ratio 欄位（0.0~1.0），
  代表過去 N 小時內有多少比例的請求是「估算值」而非 API 實際回報，
  供 $dashboard 顯示，讓 Owner 知道數字的精確程度
  （is_estimated 欄位本身已存在，僅補上彙總查詢）
"""

from __future__ import annotations

import logging
import time

from database.ai.sqlite import get_connection

logger = logging.getLogger("bot.budget")

# ── DB 初始化 ──────────────────────

def _init() -> None:
    conn = get_connection()
    c    = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS token_budget (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT    NOT NULL,
            model         TEXT    NOT NULL,
            input_tokens  INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            is_estimated  INTEGER DEFAULT 0,
            request_type  TEXT    DEFAULT 'chat',
            created_at    REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE TABLE IF NOT EXISTS error_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT,
            model      TEXT,
            error_type TEXT    NOT NULL,
            created_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_budget_user
            ON token_budget(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_budget_time
            ON token_budget(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_error_time
            ON error_log(created_at DESC);
    """)
    conn.commit()
    conn.close()


_init()

# ── 估算工具 ──────────────────────

def _estimate(text: str) -> int:
    """
    以字元數粗估 token 數。
    中英文混合平均約 3 字元 / token。
    """
    return max(1, len(text) // 3)


def _extract_tokens(res) -> tuple[int, int, bool]:
    """
    從 API response 物件取出 token 數。
    有 usage_metadata 時回傳實際值，否則估算。
    """
    usage = getattr(res, "usage_metadata", None)
    if usage:
        inp  = getattr(usage, "prompt_token_count",     None)
        out  = getattr(usage, "candidates_token_count", None)
        if inp is not None and out is not None:
            return int(inp), int(out), False   # 非估算
    return 0, 0, True   # 呼叫方補估算值

# ── 寫入 ──────────────────────

def record_usage(
    user_id:      str,
    model:        str,
    input_text:   str  = "",
    output_text:  str  = "",
    res           = None,
    request_type: str  = "chat",
) -> None:
    """
    記錄一次 API 呼叫的 Token 用量。

    res 是 API response 物件（有 usage_metadata 時使用實際值）。
    input_text / output_text 用於估算（res 無 usage_metadata 時）。
    """
    if res is not None:
        inp, out, estimated = _extract_tokens(res)
        if estimated:
            inp = _estimate(input_text)
            out = _estimate(output_text)
    else:
        inp       = _estimate(input_text)
        out       = _estimate(output_text)
        estimated = True

    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO token_budget
                (user_id, model, input_tokens, output_tokens, is_estimated, request_type)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, model, inp, out, int(estimated), request_type),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[budget] record_usage error: %s", e)


def record_error(
    error_type: str,
    user_id:    str  = "",
    model:      str  = "",
) -> None:
    """記錄一筆錯誤事件，供計算錯誤率使用。"""
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO error_log (user_id, model, error_type) VALUES (?, ?, ?)",
            (user_id, model, error_type),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("[budget] record_error error: %s", e)

# ── 查詢 ──────────────────────

def get_user_stats(user_id: str, days: int = 30) -> dict:
    """
    取得指定使用者的 Token 用量統計。

    回傳：
    {
        "total_requests": int,
        "total_input":    int,
        "total_output":   int,
        "total_tokens":   int,
        "by_model":       {model: {"requests": int, "tokens": int}, ...},
        "last_active":    float | None,   # unixepoch
    }
    """
    since = time.time() - days * 86_400
    conn  = get_connection()
    c     = conn.cursor()

    c.execute("""
        SELECT model,
               COUNT(*)                  AS requests,
               SUM(input_tokens)         AS inp,
               SUM(output_tokens)        AS out,
               MAX(created_at)           AS last
        FROM   token_budget
        WHERE  user_id = ? AND created_at >= ?
        GROUP  BY model
    """, (user_id, since))

    rows = c.fetchall()
    conn.close()

    by_model    = {}
    total_req   = 0
    total_inp   = 0
    total_out   = 0
    last_active = None

    for r in rows:
        by_model[r["model"]] = {
            "requests": r["requests"],
            "tokens":   (r["inp"] or 0) + (r["out"] or 0),
        }
        total_req   += r["requests"]
        total_inp   += r["inp"] or 0
        total_out   += r["out"] or 0
        if last_active is None or r["last"] > last_active:
            last_active = r["last"]

    return {
        "total_requests": total_req,
        "total_input":    total_inp,
        "total_output":   total_out,
        "total_tokens":   total_inp + total_out,
        "by_model":       by_model,
        "last_active":    last_active,
    }


def get_global_stats(hours: int = 24) -> dict:
    """
    取得全系統統計（過去 N 小時）。

    回傳：
    {
        "total_requests": int,
        "total_tokens":   int,
        "active_users":   int,
        "by_model":       {model: {"requests": int, "tokens": int}, ...},
        "error_count":    int,
        "error_rate":     float,    # 0.0 ~ 1.0
        "cache_hits":     int,      # 從 search_cache 計
    }
    """
    since = time.time() - hours * 3_600
    conn  = get_connection()
    c     = conn.cursor()

    c.execute("""
        SELECT model,
               COUNT(*)          AS requests,
               SUM(input_tokens + output_tokens) AS tokens
        FROM   token_budget
        WHERE  created_at >= ?
        GROUP  BY model
    """, (since,))
    rows = c.fetchall()

    c.execute(
        "SELECT COUNT(*) FROM error_log WHERE created_at >= ?", (since,),
    )
    err_count = c.fetchone()[0]

    # 快取命中數（若 search_cache 表存在）
    cache_hits = 0
    try:
        c.execute(
            "SELECT COUNT(*) FROM search_cache WHERE created_at >= ?", (since,),
        )
        cache_hits = c.fetchone()[0]
    except Exception:
        pass

    c.execute(
        "SELECT COUNT(DISTINCT user_id) FROM token_budget WHERE created_at >= ?",
        (since,),
    )
    active_users = c.fetchone()[0]

    c.execute(
        "SELECT COUNT(*) FROM token_budget WHERE created_at >= ? AND is_estimated = 1",
        (since,),
    )
    estimated_count = c.fetchone()[0]

    conn.close()

    by_model  = {}
    total_req = 0
    total_tok = 0

    for r in rows:
        by_model[r["model"]] = {
            "requests": r["requests"],
            "tokens":   r["tokens"] or 0,
        }
        total_req += r["requests"]
        total_tok += r["tokens"] or 0

    total_events    = total_req + err_count
    error_rate      = err_count / total_events if total_events else 0.0
    estimated_ratio = estimated_count / total_req if total_req else 0.0

    return {
        "total_requests":  total_req,
        "total_tokens":    total_tok,
        "active_users":    active_users,
        "by_model":        by_model,
        "error_count":     err_count,
        "error_rate":      error_rate,
        "cache_hits":      cache_hits,
        "estimated_ratio": estimated_ratio,
    }


def get_total_memory_count() -> int:
    """取得 memories 表的總記憶筆數。"""
    try:
        conn  = get_connection()
        c     = conn.cursor()
        c.execute("SELECT COUNT(*) FROM memories")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def get_total_user_count() -> int:
    """取得有過互動的使用者總數。"""
    try:
        conn  = get_connection()
        c     = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT user_id) FROM token_budget")
        count = c.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def get_top_users(limit: int = 10, days: int = 30) -> list[dict]:
    """
    取得 Token 用量前 N 位使用者。

    回傳：
    [{"user_id": str, "requests": int, "tokens": int}, ...]
    依 tokens 降序排列。
    """
    since = time.time() - days * 86_400
    try:
        conn = get_connection()
        c    = conn.cursor()
        c.execute(
            """
            SELECT user_id,
                   COUNT(*) AS requests,
                   SUM(input_tokens + output_tokens) AS tokens
            FROM   token_budget
            WHERE  created_at >= ?
            GROUP  BY user_id
            ORDER  BY tokens DESC
            LIMIT  ?
            """,
            (since, limit),
        )
        rows = c.fetchall()
        conn.close()
        return [
            {
                "user_id":  r["user_id"],
                "requests": r["requests"],
                "tokens":   r["tokens"] or 0,
            }
            for r in rows
        ]
    except Exception as e:
        logger.debug("[budget] get_top_users error: %s", e)
        return []
