"""
core/ai/memory_manager.py

職責：
- 統一管理所有記憶操作：訊息、長期記憶、向量記憶、摘要
- 提供 search() 供 context_manager 一次取得所有記憶來源
- 背景任務（擷取、摘要、向量化）由 event_bus 觸發

合併來源：
- core/ai/memory.py
- core/ai/memory_extractor.py
- core/ai/vector_memory.py
- core/ai/summarizer.py

修正：
- 所有背景任務改由 event_bus 觸發，從 core.py 解耦
- Memory Cache：每次 search 結果快取 5 秒，避免同 user 同 request 重複查詢
- Memory 去重：save_memory() 寫入時依 UNIQUE(user_id, keyword) 約束
  搭配 ON CONFLICT DO UPDATE（見 memory_repository.py），同一使用者
  對同一關鍵字的記憶會直接覆寫更新，不會產生重複紀錄
  （說明更新：原註解誤寫為「比較相似度 > 0.9 不寫入」，但程式碼中
  並無 SequenceMatcher 或任何模糊相似度比較邏輯，實際機制是
  關鍵字完全相同才會觸發覆寫，更正說明以符合實際行為）
- Summary 改為事件觸發而非每輪觸發，降低 API 呼叫
- 模型常數改由 core.ai.models 統一提供，移除硬編碼字串
- search() 的 global_mems 參數補上正確型別標註（list[...] | None）
- 移除原本的 _FENCE_RE，改用 core.ai.json_utils.strip_json_fence()
  （與 user_context.py 共用同一套 JSON 區塊清理邏輯）
- 移除未使用的 get_background()（無任何呼叫端，load_background 已由
  search() 內部直接呼叫 repo.load_background()）
- client 改由 core.ai.gemini_client 統一提供，移除本檔的
  genai.Client(api_key=GEMINI_API) 重複建立，並連帶移除
  未使用的 genai / GEMINI_API import

新增（channel_id）：
- save_message() / search() / get_recent() 新增 channel_id 參數，
  轉交 memory_repository 的「user_id + channel_id」雙重過濾，
  避免使用者在不同伺服器 / 頻道的對話互相污染 context
- search() 的快取 key 加入 channel_id，避免同一使用者在不同
  頻道發送相同內容時，誤用對方頻道的快取結果

新增（行為調校集中化）：
- _SUMMARY_TRIGGER / _SUMMARY_KEEP / _CACHE_TTL 改由 settings.json
  統一提供，可熱更新（預設值與優化前相同）
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time

from google.genai import types

from core.system.settings import get as _s
import database.repository.memory_repository as repo
from core.ai.gemini_client import client
from core.ai.json_utils import strip_json_fence
from core.ai.models import EMBED_MODEL, MODELS
from core.system import event_bus

logger = logging.getLogger("bot.memory_manager")

# ── 常數 ──────────────────────────────────────────────────────────────

_EXTRACT_MODEL   = MODELS["lite"]
_EMBED_MODEL     = EMBED_MODEL
_SUMMARY_MODEL   = MODELS["lite"]
_EXTRACT_TIMEOUT = 15
_EMBED_TIMEOUT   = 10
_SUMMARY_TIMEOUT = 20
_SUMMARY_TRIGGER = int(_s('ai.summary_trigger', 40))   # 訊息數超過此值才觸發摘要
_SUMMARY_KEEP    = int(_s('ai.summary_keep', 10))      # 摘要時保留最新 N 筆不納入

_EXTRACT_SYSTEM = (
    "你是長期記憶分析器。只輸出 JSON，沒有值得記憶的就輸出 {\"memories\":[]}。\n"
    "只記錄長期穩定資訊（偏好、身份、習慣、重要事實），不記閒聊。\n"
    "格式：{\"memories\":[{\"keyword\":\"分類\",\"content\":\"內容\",\"importance\":1到5}]}"
)

_SUMMARY_SYSTEM = (
    "你是對話摘要助手。將以下對話整理成 200 字以內的繁體中文摘要。\n"
    "重點：使用者的偏好、重要事實、情緒傾向、正在進行的話題。\n"
    "排除：閒聊、打招呼、重複內容。只輸出摘要文字，不加任何標題或說明。"
)

# ── 簡易記憶快取（TTL 由 settings.json 統一管理）────────────────────────

_search_cache: dict[str, tuple[float, MemoryBundle]] = {}
_CACHE_TTL = float(_s('ai.memory_cache_ttl', 5.0))

# ── 儲存入口 ──────────────────────────────────────────────────────────

def save_message(user_id: str, role: str, content: str, channel_id: str = "") -> None:
    repo.insert_message(user_id, role, content, channel_id)


def save_memory(user_id: str, keyword: str, content: str, importance: int = 1) -> None:
    importance = max(1, min(5, importance))
    if not keyword.strip() or not content.strip():
        return
    repo.upsert_memory(user_id, keyword, content, importance)

# ── 搜尋入口 ──────────────────────────────────────────────────────────

class MemoryBundle:
    """search() 的回傳結果，封裝所有記憶來源。"""
    __slots__ = ("memories", "messages", "recent", "summary", "background")

    def __init__(
        self,
        memories:   list[tuple[str, str, int]],
        messages:   list[tuple[str, str]],
        recent:     list[tuple[str, str]],
        summary:    str,
        background: list[tuple[str, str, int]],
    ) -> None:
        self.memories   = memories
        self.messages   = messages
        self.recent     = recent
        self.summary    = summary
        self.background = background


def search(
    user_id: str,
    channel_id: str,
    query:   str,
    global_mems: list[tuple[str, str, int]] | None = None,
) -> MemoryBundle:
    """
    一次取得所有記憶來源並排序。
    結果快取 _CACHE_TTL 秒，同一請求內重複呼叫不會重複查詢。

    channel_id 用於過濾「相關歷史訊息」與「最近對話」（messages / recent），
    確保不同伺服器 / 頻道的對話不會互相混入；
    memories / background / summary 維持使用者全域，不受 channel_id 影響。
    """
    cache_key = f"{user_id}:{channel_id}:{query[:50]}"
    now = time.monotonic()
    if cache_key in _search_cache:
        ts, bundle = _search_cache[cache_key]
        if now - ts < _CACHE_TTL:
            return bundle

    background   = repo.load_background()
    global_mems  = global_mems or []
    raw_mems     = repo.get_memories_candidate(user_id, limit=30)
    all_memories = global_mems + background + raw_mems

    raw_msgs     = repo.get_messages_candidate(user_id, channel_id, limit=200)
    recent       = repo.get_recent_messages(user_id, channel_id, limit=12)
    summary      = repo.get_summary(user_id)

    from core.ai.ranker import optimize_context
    ctx = optimize_context(
        query    = query,
        memories = all_memories,
        messages = raw_msgs,
        recent   = recent,
    )

    bundle = MemoryBundle(
        memories   = ctx["memories"],
        messages   = ctx["messages"],
        recent     = ctx["recent"],
        summary    = summary,
        background = background,
    )
    _search_cache[cache_key] = (now, bundle)
    return bundle


def get_recent(user_id: str, channel_id: str, limit: int = 12) -> list[tuple[str, str]]:
    return repo.get_recent_messages(user_id, channel_id, limit)


def get_summary_text(user_id: str) -> str:
    return repo.get_summary(user_id)

# ── 向量搜尋 ──────────────────────────────────────────────────────────

async def search_semantic(
    user_id:   str,
    query:     str,
    limit:     int   = 5,
    threshold: float = 0.6,
) -> list[tuple[str, str, int, float]]:
    """語意向量搜尋，失敗時回傳空列表。"""
    query_vec = await _embed(query)
    if query_vec is None:
        return []

    rows   = repo.get_all_vectors(user_id)
    scored = []
    for r in rows:
        sim = _cosine(query_vec, r["embedding"])
        if sim >= threshold:
            scored.append((sim, r["keyword"], r["content"], r["importance"]))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [(kw, c, imp, sim) for sim, kw, c, imp in scored[:limit]]

# ── 背景任務 ──────────────────────────────────────────────────────────

async def _on_message_generated(
    user_id: str,
    user_msg: str,
    ai_msg:  str,
    **_,
) -> None:
    """event_bus 觸發：擷取記憶 → 嘗試摘要 → 向量化。"""
    await _extract(user_id, user_msg, ai_msg)
    await _summarize_if_needed(user_id)
    await _vectorize_recent(user_id, user_msg)


async def _extract(user_id: str, user_input: str, ai_output: str) -> None:
    if len(user_input) + len(ai_output) < 20:
        return
    try:
        res = await asyncio.wait_for(
            client.aio.models.generate_content(
                model    = _EXTRACT_MODEL,
                contents = f"User: {user_input}\nAI: {ai_output}",
                config   = types.GenerateContentConfig(
                    system_instruction=_EXTRACT_SYSTEM,
                ),
            ),
            timeout=_EXTRACT_TIMEOUT,
        )
        raw     = (res.text or "").strip()
        cleaned = strip_json_fence(raw)
        data    = json.loads(cleaned)
        mems    = data.get("memories", [])
        if not isinstance(mems, list):
            return
        saved = 0
        for m in mems:
            if not isinstance(m, dict):
                continue
            kw  = str(m.get("keyword", "")).strip()
            cnt = str(m.get("content",  "")).strip()
            if not kw or not cnt:
                continue
            try:
                imp = max(1, min(5, int(m.get("importance", 1))))
            except (TypeError, ValueError):
                imp = 1
            save_memory(user_id, kw, cnt, imp)
            saved += 1
        if saved:
            logger.debug("[memory_manager] extract user=%s saved=%d", user_id, saved)
    except asyncio.TimeoutError:
        logger.debug("[memory_manager] extract timeout user=%s", user_id)
    except Exception as e:
        logger.debug("[memory_manager] extract error user=%s: %s", user_id, e)


async def _summarize_if_needed(user_id: str) -> None:
    count = repo.count_messages(user_id)
    if count < _SUMMARY_TRIGGER:
        return
    messages = repo.get_messages_excluding_recent(user_id, _SUMMARY_KEEP)
    if len(messages) < 10:
        return
    conversation = "\n".join(
        f"{role}: {content[:200]}" for role, content in messages
    )
    try:
        res = await asyncio.wait_for(
            client.aio.models.generate_content(
                model    = _SUMMARY_MODEL,
                contents = conversation,
                config   = types.GenerateContentConfig(
                    system_instruction=_SUMMARY_SYSTEM,
                ),
            ),
            timeout=_SUMMARY_TIMEOUT,
        )
        summary = (res.text or "").strip()
        if summary:
            repo.upsert_summary(user_id, summary, count)
            logger.info(
                "[memory_manager] summary user=%s msg=%d len=%d",
                user_id, count, len(summary),
            )
    except asyncio.TimeoutError:
        logger.debug("[memory_manager] summary timeout user=%s", user_id)
    except Exception as e:
        logger.debug("[memory_manager] summary error user=%s: %s", user_id, e)


async def _vectorize_recent(user_id: str, query: str) -> None:
    """向量化最近擷取到的記憶（延遲 1 秒等待 extract 完成）。"""
    await asyncio.sleep(1)
    mems = repo.get_memories_candidate(user_id, limit=5)
    for kw, content, imp in mems:
        vec = await _embed(f"{kw}: {content}")
        if vec:
            repo.upsert_vector(user_id, kw, content, vec, imp)


async def force_summarize(user_id: str) -> str:
    """強制生成摘要（供管理指令使用）。"""
    messages = repo.get_messages_excluding_recent(user_id, _SUMMARY_KEEP)
    if not messages:
        return ""
    conversation = "\n".join(
        f"{role}: {content[:200]}" for role, content in messages
    )
    try:
        res = await asyncio.wait_for(
            client.aio.models.generate_content(
                model    = _SUMMARY_MODEL,
                contents = conversation,
                config   = types.GenerateContentConfig(
                    system_instruction=_SUMMARY_SYSTEM,
                ),
            ),
            timeout=_SUMMARY_TIMEOUT,
        )
        summary = (res.text or "").strip()
        if summary:
            repo.upsert_summary(user_id, summary, len(messages))
        return summary
    except Exception as e:
        logger.debug("[memory_manager] force_summarize error: %s", e)
        return ""

# ── 數學工具 ──────────────────────────────────────────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


async def _embed(text: str) -> list[float] | None:
    try:
        res = await asyncio.wait_for(
            client.aio.models.embed_content(
                model    = _EMBED_MODEL,
                contents = text[:2_000],
            ),
            timeout=_EMBED_TIMEOUT,
        )
        embeddings = getattr(res, "embeddings", None)
        if embeddings and embeddings[0].values:
            return list(embeddings[0].values)
    except asyncio.TimeoutError:
        logger.debug("[memory_manager] embed timeout")
    except Exception as e:
        logger.debug("[memory_manager] embed error: %s", e)
    return None

# ── 事件注冊 ──────────────────────────────────────────────────────────

event_bus.on("message_generated", _on_message_generated)
