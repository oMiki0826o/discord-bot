"""
core/ai/ranker.py

職責：
- 對記憶與歷史訊息依相關性排序
- optimize_context 整合三個來源後回傳供 prompt_builder.build 使用

修正：
- memories 統一使用 3-tuple (keyword, content, importance)
- _score 採詞彙交集 × 2 + importance 加權，避免無關記憶因 importance 高而排前
- 移除舊版 context_optimizer.py（此檔取代之）
"""

from __future__ import annotations

# ── 評分 ─────────────────────────────────────────────────────────────

def _score(query: str, text: str, importance: int = 1) -> float:
    """
    詞彙交集分數 + importance 加權。

    base  = 共同出現詞彙數 × 2
    total = base + importance
    ── importance 以加法計入，不做乘法，
       避免完全不相關的高重要度記憶排名超過相關的低重要度記憶
    """
    q = set(query.lower().split())
    t = set(text.lower().split())
    return len(q & t) * 2 + importance

# ── 記憶排序 ──────────────────────────────────────────────────────────

def rank_memories(
    query:    str,
    memories: list[tuple[str, str, int]],
    limit:    int = 6,
) -> list[tuple[str, str, int]]:
    """
    輸入：[(keyword, content, importance), ...]
    回傳：同格式，依相關性降序，取前 limit 筆。
    """
    scored = [
        (_score(query, f"{kw} {content}", imp), kw, content, imp)
        for kw, content, imp in memories
    ]
    scored.sort(reverse=True, key=lambda x: x[0])
    return [(kw, c, imp) for _, kw, c, imp in scored[:limit]]

# ── 訊息排序 ──────────────────────────────────────────────────────────

def rank_messages(
    query:    str,
    messages: list[tuple[str, str]],
    limit:    int = 8,
) -> list[tuple[str, str]]:
    """
    輸入：[(role, content), ...]
    回傳：同格式，依相關性降序，取前 limit 筆。
    """
    scored = [
        (_score(query, content), role, content)
        for role, content in messages
    ]
    scored.sort(reverse=True, key=lambda x: x[0])
    return [(role, content) for _, role, content in scored[:limit]]

# ── 整合 ──────────────────────────────────────────────────────────────

def optimize_context(
    query:    str,
    memories: list[tuple[str, str, int]],
    messages: list[tuple[str, str]],
    recent:   list[tuple[str, str]],
) -> dict:
    """
    整合三個來源後回傳，供 prompt_builder.build 使用。

    memories → rank_memories（相關性 + importance 加權排序）
    messages → rank_messages（相關性排序）
    recent   → 直接取最後 6 筆，維持時間正序，不重新排序
    """
    return {
        "memories": rank_memories(query, memories),
        "messages": rank_messages(query, messages),
        "recent":   list(recent[-6:]),
    }
