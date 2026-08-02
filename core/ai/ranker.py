"""
core/ai/ranker.py

Modification():
- 修正 optimize_context()：移除 recent[-6:] 這個寫死且無設定可調的
  二次截斷。呼叫端 memory_manager.search() 已用 ai.recent_message_limit
  （settings.json 可調整，預設 12）限制過筆數，這裡再砍一次固定的
  「6」，會讓使用者調整 recent_message_limit 之後毫無效果——查了半天
  設定卻永遠只看得到 6 筆最近對話。改為直接信任呼叫端傳入的筆數。
- 對記憶與歷史訊息依相關性排序。
- 增加 query / content / importance 的型別防護，避免非字串資料造成 runtime crash。
- 保留詞彙交集加權排序，避免無關但高 importance 的記憶過度前排。

職責：
- 將 memory_manager 取回的候選資料整理成 prompt_builder 可直接使用的排序結果。
- 對外提供 optimize_context() 作為 context 排序入口。
"""

from __future__ import annotations

from collections.abc import Iterable

# ── 評分 ──────────────────────

def _as_text(value: object) -> str:
    """將外部資料安全轉成文字，避免 list / None 等資料造成 lower() 崩潰。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    if isinstance(value, dict):
        return " ".join(f"{k} {v}" for k, v in value.items())
    if isinstance(value, Iterable):
        return " ".join(_as_text(item) for item in value)
    return str(value)


def _as_importance(value: object) -> int:
    """將 importance 正規化為 1 到 5，避免資料庫或外部來源傳入異常值。"""
    try:
        importance = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(5, importance))


def _score(query: object, text: object, importance: object = 1) -> float:
    """
    詞彙交集分數 + importance 加權。

    base  = 共同出現詞彙數 × 2
    total = base + importance
    ── importance 以加法計入，不做乘法，
       避免完全不相關的高重要度記憶排名超過相關的低重要度記憶
    """
    q = set(_as_text(query).lower().split())
    t = set(_as_text(text).lower().split())
    return len(q & t) * 2 + _as_importance(importance)

# ── 記憶排序 ──────────────────────

def rank_memories(
    query:    object,
    memories: list[tuple[str, str, int]],
    limit:    int = 6,
) -> list[tuple[str, str, int]]:
    """
    輸入：[(keyword, content, importance), ...]
    回傳：同格式，依相關性降序，取前 limit 筆。
    """
    scored = [
        (_score(query, f"{_as_text(kw)} {_as_text(content)}", imp),
         _as_text(kw), _as_text(content), _as_importance(imp))
        for kw, content, imp in memories
    ]
    scored.sort(reverse=True, key=lambda x: x[0])
    return [(kw, c, imp) for _, kw, c, imp in scored[:limit]]

# ── 訊息排序 ──────────────────────

def rank_messages(
    query:    object,
    messages: list[tuple[str, str]],
    limit:    int = 8,
) -> list[tuple[str, str]]:
    """
    輸入：[(role, content), ...]
    回傳：同格式，依相關性降序，取前 limit 筆。
    """
    scored = [
        (_score(query, content), _as_text(role), _as_text(content))
        for role, content in messages
    ]
    scored.sort(reverse=True, key=lambda x: x[0])
    return [(role, content) for _, role, content in scored[:limit]]

# ── 整合 ──────────────────────

def optimize_context(
    query:    object,
    memories: list[tuple[str, str, int]],
    messages: list[tuple[str, str]],
    recent:   list[tuple[str, str]],
) -> dict:
    """
    整合三個來源後回傳，供 prompt_builder.build 使用。

    memories → rank_memories（相關性 + importance 加權排序）
    messages → rank_messages（相關性排序）
    recent   → 直接沿用呼叫端傳入的內容，維持時間正序，不重新排序、
               也不在此再次截斷。

    修正：原本這裡固定寫死 recent[-6:]，但呼叫端
    memory_manager.search() 早已用 ai.recent_message_limit（settings.json
    可調整，預設 12）限制過 recent 的筆數；這裡又用另一個沒有對應設定、
    寫死在程式碼裡的「6」重新砍一次，等於使用者把 recent_message_limit
    調成 12，實際能用到的「最近對話」卻永遠只有 6 筆，設定值形同虛設。
    直接信任呼叫端已經處理過筆數限制，不在這裡重複、不一致地再限制一次。
    """
    return {
        "memories": rank_memories(query, memories),
        "messages": rank_messages(query, messages),
        "recent":   list(recent),
    }
