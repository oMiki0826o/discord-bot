"""
core/ai/memory_manager.py

Modification():
- 因應 database/repository/memory_repository.py 全面套用
  utils.async_db.to_thread，本檔幾乎每個對外函式都改為 async def
  並加上 await（save_message / save_memory / search / get_recent /
  get_summary_text，以及原本就是 async 但缺 await 的
  search_semantic / _extract / _summarize_if_needed /
  _vectorize_recent / force_summarize）。
  search() 內五個彼此獨立的查詢（background / raw_mems / raw_msgs /
  recent / summary）改用 asyncio.gather() 平行執行，而非依序 await，
  總等待時間取決於最慢的一個查詢，而非全部查詢時間總和。
  呼叫端 core/ai/context_manager.py 原本用
  loop.run_in_executor(None, memory_search, ...) 把整個同步的
  search() 丟到執行緒池執行；search() 本身變成 async def 後不再
  需要這層包裝，直接 await，呼叫端一併簡化。
  cogs/ai/ai_owner_commands.py、cogs/events/message.py 等呼叫
  save_message / force_summarize 等函式的地方已同步更新為 await。
- 記憶快取、摘要門檻、候選數量與逾時秒數改為使用時讀取 settings.json。
- 移除 import 時固定的設定常數，讓熱更新設定能真正影響記憶流程。
- 背景任務仍由 event_bus 觸發，保持 core.py 與記憶副作用解耦。

Description():

- 本檔統一管理訊息、長期記憶、向量記憶與摘要。
- search() 供 context_manager 一次取得所有記憶來源，背景擷取與摘要由事件觸發。

合併來源：
- core/ai/memory.py
- core/ai/memory_extractor.py
- core/ai/vector_memory.py
- core/ai/summarizer.py

修正：
- 所有背景任務改由 event_bus 觸發，從 core.py 解耦
- Memory Cache：每次 search 結果快取 5 秒，避免同 user 同 request 重複查詢
- Memory 去重與更新改以 user_id + channel_id + content_hash 判斷；
  單值欄位（例如 nickname）保留版本並將舊值標記 superseded，
  作廢／刪除的記憶不再進入檢索與向量結果。
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
- 摘要、快取、候選數量與模型逾時均由 settings.json 統一提供，可熱更新。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
import hashlib
import json
import logging
import math
import re
import time
import uuid

from google.genai import types

import database.repository.memory_repository as repo
from core.ai.gemini_client import client
from core.ai.json_utils import strip_json_fence
from core.ai.models import EMBED_MODEL, MODELS
from core.ai.quota_manager import (
    background_request,
    error_status_code,
    foreground_request,
    is_quota_error,
    mark_quota_exhausted_from_error,
)
from core.system import event_bus
from core.system.settings import get_float, get_int

logger = logging.getLogger("bot.memory_manager")

# ── 常數 ──────────────────────

_EXTRACT_MODEL   = MODELS["lite"]
_EMBED_MODEL     = EMBED_MODEL
_SUMMARY_MODEL   = MODELS["lite"]

_EXTRACT_SYSTEM = """
你是長期記憶篩選器，只能輸出合法 JSON，不得輸出 Markdown、前言或說明。

只擷取由使用者明確陳述、長期穩定且未來仍有協助價值的資訊，例如長期偏好、穩定溝通習慣、未來有用的身份資訊與長期專案的已確認決定。
不得記錄 AI 回覆中的推測或角色表演、閒聊、一次性要求、未確認推論、敏感屬性推測，也不得記錄 API Key、Token、密碼、Cookie、Session、私鑰、.env、驗證碼、精確地址、電話或金融資料。
無法確認資訊是否由目前使用者陳述時，不得記錄。

沒有適合資訊時輸出：{"memories":[]}
每筆記憶必須能在 current_user_message 找到逐字 source_excerpt，找不到就不得輸出。
scope_type 只能是 user 或 channel：暱稱、個人偏好、個人身份、個人專案使用 user，可跨頻道延續；只有頻道規則、頻道共同決定或頻道事件使用 channel。不得只因訊息出現在頻道就選 channel。
若使用者明確更新單值資料（例如暱稱、稱呼、語言、時區），operation 使用 replace；一般新增使用 create；明確要求忘記某類既有記憶時使用 delete。
輸出格式：{"memories":[{"operation":"create|replace|delete","scope_type":"user|channel","keyword":"簡短分類","category":"preference|identity|project|decision|task|general","subject":"穩定欄位或主題，例如 nickname","content":"一條第三人稱原子事實；delete 時描述要刪除的目標","importance":1,"confidence":"high","source_excerpt":"使用者原句中的逐字片段","single_value":false}]}
importance 為 1～5 的整數；confidence 只能是 high、medium 或 low，low 不應儲存。1為較不重要，5為較重要應最少。
""".strip()

_SUMMARY_SYSTEM = """
你是對話摘要器。請將對話整理成 200 字以內的繁體中文摘要。
優先保留使用者明確表達的偏好與事實、已做出的決定、未完成的任務與下一步，以及延續對話必要的上下文。
必須區分使用者陳述、AI 建議與尚未確認的推測。
排除打招呼、閒聊、重複內容、秘密憑證、無關敏感資訊與模型自行推測的敏感屬性。
請依內容使用以下 Markdown 區塊，沒有內容的區塊必須省略：
**一般延續：**、**當前事實與狀態：**、**社交資訊：**、**專案上下文：**、**待辦任務：**。
專案與待辦必須放在各自區塊，不得混入一般延續或事實區塊。
每筆待辦必須寫明所屬專案、對象或主題名稱，不得只寫「繼續處理」之類無法獨立檢索的文字。
只輸出上述摘要區塊，不加前言或額外說明。
""".strip()

# ── 簡易記憶快取（TTL 由 settings.json 統一管理） ──────────────────────

_search_cache: dict[str, tuple[float, MemoryBundle]] = {}
_memory_jobs_in_progress: set[str] = set()
_memory_pending_jobs: dict[str, deque[tuple[str, str, str, str, int | None]]] = defaultdict(deque)

_RETRACT_RE = re.compile(
    r"(?:前|上)(?:一)?項.*(?:作廢|取消|刪除)|"
    r"(?:作廢|取消|刪除|忘掉).*(?:前|上)(?:一)?項|剛才.*(?:作廢|取消)",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"(?:api[_ -]?key|token|password|passwd|cookie|session|private[_ -]?key|"
    r"驗證碼|密碼|私鑰)\s*[:=：]",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"\b(?:AIza[A-Za-z0-9_-]{30,}|sk-[A-Za-z0-9_-]{20,}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{10,})\b|"
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|"
    r"(?<!\d)(?:\+?886[- ]?)?09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)",
    re.IGNORECASE,
)
_RECALL_RE = re.compile(
    r"記得|之前|上次|曾經|我喜歡|我偏好|適合我|remember|before|last time",
    re.IGNORECASE,
)
_MEMORY_CUE_RE = re.compile(
    r"我(?:的|是|有|會|喜歡|討厭|偏好|習慣|正在|決定)|"
    r"請記|記住|忘記|刪除|清除|以後|不要再|改成|改為|固定|長期|專案|"
    r"i\s+(?:am|have|like|prefer|usually|always|never)",
    re.IGNORECASE,
)
_NICKNAME_RE = re.compile(
    r"(?:我的)?(?:暱稱|稱呼|名字)\s*(?:是|叫|改成|改為|用)\s*[「『\"']?"
    r"(?P<value>[^。！？!?，,\n「」『』\"']{1,40})|"
    r"(?:以後\s*)?(?:請\s*)?(?:叫我|稱呼我)\s*[「『\"']?"
    r"(?P<called>[^。！？!?，,\n「」『』\"']{1,40})",
    re.IGNORECASE,
)
_FORGET_NICKNAME_RE = re.compile(
    r"(?:忘記|刪除|清除|不要記得).*(?:暱稱|稱呼|名字)|"
    r"(?:暱稱|稱呼|名字).*(?:忘記|刪除|清除|作廢)",
    re.IGNORECASE,
)

# ── 設定讀取 ──────────────────────

def _summary_trigger() -> int:
    return max(1, get_int("ai.summary_trigger", 40))


def _summary_keep() -> int:
    return max(0, get_int("ai.summary_keep", 10))


def _summary_min_messages() -> int:
    return max(1, get_int("ai.summary_min_messages", 10))


def _summary_new_message_trigger() -> int:
    return max(1, get_int("ai.memory_summary_new_message_trigger", 10))


def _summary_line_max_chars() -> int:
    return max(1, get_int("ai.summary_line_max_chars", 200))


def _cache_ttl() -> float:
    return max(0.0, get_float("ai.memory_cache_ttl", 5.0))


def _extract_timeout() -> float:
    return max(1.0, get_float("ai.memory_extract_timeout_seconds", 15.0))


def _embed_timeout() -> float:
    return max(1.0, get_float("ai.memory_embed_timeout_seconds", 10.0))


def _summary_timeout() -> float:
    return max(1.0, get_float("ai.memory_summary_timeout_seconds", 20.0))


def _min_extract_chars() -> int:
    return max(0, get_int("ai.memory_min_extract_chars", 20))


def _embedding_max_chars() -> int:
    return max(1, get_int("ai.memory_embedding_max_chars", 2000))


def _memory_candidate_limit() -> int:
    return max(1, get_int("ai.memory_candidate_limit", 200))


def _message_candidate_limit() -> int:
    return max(1, get_int("ai.message_candidate_limit", 200))


def _recent_message_limit() -> int:
    return max(1, get_int("ai.recent_message_limit", 12))


def _vector_candidate_limit() -> int:
    return max(1, get_int("ai.vector_candidate_limit", 5))


def _vectorize_delay_seconds() -> float:
    return max(0.0, get_float("ai.memory_vectorize_delay_seconds", 1.0))

# ── 儲存入口 ──────────────────────

async def save_message(user_id: str, role: str, content: str, channel_id: str = "") -> int | None:
    message_id = await repo.insert_message(user_id, role, content, channel_id)
    _invalidate_search_cache(user_id, channel_id)
    return message_id


async def save_conversation_turn(
    user_id: str,
    user_content: str,
    assistant_content: str,
    channel_id: str = "",
    turn_id: str = "",
) -> tuple[int, int]:
    turn_id = turn_id or uuid.uuid4().hex
    ids = await repo.insert_conversation_turn(
        user_id, user_content, assistant_content, channel_id, turn_id,
    )
    _invalidate_search_cache(user_id, channel_id)
    return ids


async def save_memory(
    user_id: str,
    keyword: str,
    content: str,
    importance: int = 1,
    channel_id: str = "",
    **metadata,
) -> int | None:
    importance = max(1, min(5, importance))
    if not keyword.strip() or not content.strip():
        return None
    metadata.setdefault("scope_type", "user")
    memory_id = await repo.upsert_memory(
        user_id, keyword, content, importance, channel_id, **metadata,
    )
    scope_type = str(metadata["scope_type"])
    _invalidate_search_cache(
        user_id,
        "" if scope_type == "user" else channel_id,
        shared_channel=scope_type != "user",
    )
    return memory_id


def _invalidate_search_cache(
    user_id: str,
    channel_id: str = "",
    *,
    shared_channel: bool = False,
) -> None:
    prefix = f"{user_id}:{channel_id}:"
    for key in tuple(_search_cache):
        parts = key.split(":", 2)
        same_channel = len(parts) > 1 and parts[1] == channel_id
        if (
            key.startswith(prefix)
            or (not channel_id and key.startswith(f"{user_id}:"))
            or (shared_channel and same_channel)
        ):
            _search_cache.pop(key, None)


def clear_search_cache() -> None:
    """全域／背景記憶更新時清除所有短期搜尋結果。"""
    _search_cache.clear()


async def _extract_deterministic_identity(
    user_id: str,
    user_input: str,
    channel_id: str,
    source_message_id: int | None,
) -> bool:
    """處理暱稱等明確單值欄位，不依賴模型猜測更新語意。"""
    match = _NICKNAME_RE.search(user_input)
    if not match:
        return False
    value = (match.group("value") or match.group("called") or "").strip()
    value = re.sub(r"(?:就好|即可|就可以|吧|喔|哦)$", "", value).strip()
    if not value:
        return False
    excerpt = match.group(0).strip()
    content = f"使用者希望被稱為「{value}」"
    digest = hashlib.sha256(content.casefold().encode("utf-8")).hexdigest()
    await save_memory(
        user_id,
        "暱稱",
        content,
        5,
        channel_id,
        category="identity",
        subject="nickname",
        scope_type="user",
        confidence=0.99,
        source_message_id=source_message_id,
        source_excerpt=excerpt,
        content_hash=digest,
        single_value=True,
    )
    return True

# ── 搜尋入口 ──────────────────────

class MemoryBundle:
    """search() 的回傳結果，封裝所有記憶來源。"""
    __slots__ = (
        "memories", "memory_details", "messages", "recent", "summary", "background",
    )

    def __init__(
        self,
        memories:   list[tuple[str, str, int]],
        messages:   list[tuple[str, str]],
        recent:     list[tuple[str, str]],
        summary:    str,
        background: list[tuple[str, str, int]],
        memory_details: list[dict] | None = None,
    ) -> None:
        self.memories   = memories
        self.messages   = messages
        self.recent     = recent
        self.summary    = summary
        self.background = background
        self.memory_details = memory_details or []


async def search(
    user_id: str,
    channel_id: str,
    query:   str,
    global_mems: list[tuple[str, str, int]] | None = None,
) -> MemoryBundle:
    """
    一次取得所有記憶來源並排序。
    結果快取 ai.memory_cache_ttl 秒，同一請求內重複呼叫不會重複查詢。

    channel_id 用於過濾短期訊息與摘要，避免不同場合的對話互相混入。
    長期記憶則合併「目前使用者的個人記憶」與「目前頻道的共享記憶」：
    個人記憶可跨伺服器／頻道延續，頻道事件只在原頻道延續。
    """
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:20]
    cache_key = f"{user_id}:{channel_id}:{query_hash}"
    now = time.monotonic()
    if len(_search_cache) > 512:
        ttl = _cache_ttl()
        for key, (created_at, _bundle) in tuple(_search_cache.items()):
            if now - created_at >= ttl:
                _search_cache.pop(key, None)
        while len(_search_cache) > 512:
            _search_cache.pop(next(iter(_search_cache)))
    if cache_key in _search_cache:
        ts, bundle = _search_cache[cache_key]
        if now - ts < _cache_ttl():
            return bundle

    global_mems = global_mems or []

    # 五個查詢彼此獨立（互不依賴對方的結果），用 asyncio.gather
    # 平行執行，總等待時間取決於最慢的一個查詢，而非全部查詢時間總和。
    background, memory_records, raw_msgs, recent, summary = await asyncio.gather(
        repo.load_background(),
        repo.get_memory_records(user_id, channel_id, limit=_memory_candidate_limit()),
        repo.get_messages_candidate(user_id, channel_id, limit=_message_candidate_limit()),
        repo.get_recent_messages(user_id, channel_id, limit=_recent_message_limit()),
        repo.get_summary(user_id, channel_id),
    )
    raw_mems = [
        (row["keyword"], row["content"], row["importance"])
        for row in memory_records
    ]
    all_memories = global_mems + background + raw_mems

    # 語意搜尋只在使用者明確回憶或文字候選不足時啟用，避免每一輪都產生
    # query embedding。結果仍會經過相同去重與相關性限制。
    semantic: list[tuple[str, str, int, float]] = []
    if _RECALL_RE.search(query) and memory_records:
        semantic = await search_semantic(
            user_id, query, channel_id=channel_id,
            limit=_vector_candidate_limit(),
        )
        seen = {(kw, content) for kw, content, _ in all_memories}
        for kw, content, importance, _similarity in semantic:
            if (kw, content) not in seen:
                all_memories.append((kw, content, importance))
                seen.add((kw, content))

    from core.ai.ranker import optimize_context
    ctx = optimize_context(
        query    = query,
        memories = all_memories,
        messages = raw_msgs,
        recent   = recent,
    )
    # 純語意命中可能與查詢沒有任何字面重疊，不能再次被詞彙 ranker
    # 丟棄。只補入達門檻且尚未存在的結果，總數仍限制為 6 筆。
    selected_keys = {(kw, content) for kw, content, _ in ctx["memories"]}
    for kw, content, importance, _similarity in semantic:
        if len(ctx["memories"]) >= 6:
            break
        if (kw, content) not in selected_keys:
            ctx["memories"].append((kw, content, importance))
            selected_keys.add((kw, content))

    selected_details = [
        row for row in memory_records
        if (row["keyword"], row["content"], row["importance"]) in ctx["memories"]
    ]
    detail_keys = {(row["keyword"], row["content"]) for row in selected_details}
    background_keys = {(kw, content) for kw, content, _ in background}
    for kw, content, importance in ctx["memories"]:
        if (kw, content) in detail_keys:
            continue
        selected_details.append({
            "id": "",
            "keyword": kw,
            "category": "persona_background" if (kw, content) in background_keys else "global",
            "content": content,
            "importance": importance,
            "confidence": 1.0,
            "status": "active",
        })

    bundle = MemoryBundle(
        memories   = ctx["memories"],
        messages   = ctx["messages"],
        recent     = ctx["recent"],
        summary    = summary,
        background = background,
        memory_details = selected_details,
    )
    _search_cache[cache_key] = (now, bundle)
    return bundle


async def get_recent(user_id: str, channel_id: str, limit: int = 12) -> list[tuple[str, str]]:
    return await repo.get_recent_messages(user_id, channel_id, limit)


async def get_summary_text(user_id: str, channel_id: str = "") -> str:
    return await repo.get_summary(user_id, channel_id)

# ── 向量搜尋 ──────────────────────

async def search_semantic(
    user_id:   str,
    query:     str,
    channel_id: str = "",
    limit:     int   = 5,
    threshold: float | None = None,
) -> list[tuple[str, str, int, float]]:
    """語意向量搜尋，失敗時回傳空列表。"""
    if threshold is None:
        threshold = get_float("ai.memory_semantic_threshold", 0.68)
    query_vec = await _embed(query)
    if query_vec is None:
        return []

    rows   = await repo.get_all_vectors(user_id, channel_id)
    scored = []
    for r in rows:
        sim = _cosine(query_vec, r["embedding"])
        if sim >= threshold:
            scored.append((sim, r["keyword"], r["content"], r["importance"]))

    scored.sort(reverse=True, key=lambda x: x[0])
    return [(kw, c, imp, sim) for sim, kw, c, imp in scored[:limit]]

# ── 背景任務 ──────────────────────

async def _on_message_generated(
    user_id: str,
    user_msg: str,
    ai_msg:  str,
    channel_id: str = "",
    user_message_id: int | None = None,
    **_,
) -> None:
    """event_bus 觸發：擷取記憶 → 嘗試摘要 → 向量化。"""
    job_key = f"{user_id}:{channel_id}"
    if job_key in _memory_jobs_in_progress:
        # 每一筆都保留，避免「設定暱稱 → 前項作廢 → 重新設定」在快速
        # 連續互動時只剩最後一項，造成記憶狀態錯亂。
        _memory_pending_jobs[job_key].append(
            (user_id, user_msg, ai_msg, channel_id, user_message_id),
        )
        logger.debug(
            "[memory_manager] queued background job user=%s channel=%s",
            user_id, channel_id,
        )
        return
    _memory_jobs_in_progress.add(job_key)
    try:
        current: tuple[str, str, str, str, int | None] | None = (
            user_id, user_msg, ai_msg, channel_id, user_message_id,
        )
        while current is not None:
            (
                current_user, current_user_msg, current_ai_msg,
                current_channel, current_message_id,
            ) = current
            try:
                if _FORGET_NICKNAME_RE.search(current_user_msg):
                    forgotten = await repo.forget_memory_slot(
                        current_user, current_channel, "identity", "nickname", "user",
                    )
                    if forgotten:
                        _invalidate_search_cache(current_user)
                        logger.info(
                            "[memory_manager] forgot nickname user=%s channel=%s ids=%s",
                            current_user, current_channel, forgotten,
                        )
                elif _RETRACT_RE.search(current_user_msg) and current_message_id is not None:
                    retracted = await repo.retract_memories_from_previous_user_message(
                        current_user, current_channel, current_message_id,
                    )
                    if retracted:
                        # 上一項可能同時含個人與頻道記憶，兩種 scope 都清除。
                        _invalidate_search_cache(current_user)
                        _invalidate_search_cache(
                            current_user, current_channel, shared_channel=True,
                        )
                        logger.info(
                            "[memory_manager] retracted user=%s channel=%s ids=%s",
                            current_user, current_channel, retracted,
                        )
                else:
                    deterministic = await _extract_deterministic_identity(
                        current_user,
                        current_user_msg,
                        current_channel,
                        current_message_id,
                    )
                    if not deterministic:
                        await _extract(
                            current_user,
                            current_user_msg,
                            current_ai_msg,
                            current_channel,
                            current_message_id,
                        )
                await _summarize_if_needed(current_user, current_channel)
                await _vectorize_recent(current_user, current_channel)
            except Exception as e:
                # Individual model operations log their own provider failures.
                # This boundary also exposes repository/orchestration errors.
                logger.exception(
                    "[memory_manager] operation=background_job status=%s "
                    "user=%s channel=%s error=%s",
                    error_status_code(e) or "exception",
                    current_user,
                    current_channel,
                    e,
                )
            pending = _memory_pending_jobs[job_key]
            current = pending.popleft() if pending else None
    finally:
        _memory_pending_jobs.pop(job_key, None)
        _memory_jobs_in_progress.discard(job_key)


async def _extract(
    user_id: str,
    user_input: str,
    ai_output: str,
    channel_id: str = "",
    source_message_id: int | None = None,
) -> None:
    if len(user_input.strip()) < max(4, _min_extract_chars() // 2):
        return
    if not _MEMORY_CUE_RE.search(user_input):
        return
    if _SECRET_RE.search(user_input) or _SENSITIVE_VALUE_RE.search(user_input):
        logger.info("[memory_manager] secret-like input skipped user=%s", user_id)
        return
    try:
        async with background_request(_EXTRACT_MODEL) as allowed:
            if not allowed:
                logger.debug("[memory_manager] extract deferred user=%s", user_id)
                return
            res = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model    = _EXTRACT_MODEL,
                    contents = (
                        f'<current_user_message author_id="{user_id}">\n'
                        f"{user_input}\n</current_user_message>\n\n"
                        f"<assistant_response>\n{ai_output}\n</assistant_response>"
                    ),
                    config   = types.GenerateContentConfig(
                        system_instruction=_EXTRACT_SYSTEM,
                    ),
                ),
                timeout=_extract_timeout(),
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
            excerpt = str(m.get("source_excerpt", "")).strip()
            if not kw or not cnt or not excerpt or excerpt not in user_input:
                continue
            from core.ai.ranker import relevance_score
            if relevance_score(excerpt, cnt) <= 0:
                continue
            confidence = str(m.get("confidence", "medium")).strip().casefold()
            if confidence == "low":
                continue
            confidence_value = 0.95 if confidence == "high" else 0.7
            category = str(m.get("category", "general")).strip().casefold()
            if category not in {"preference", "identity", "project", "decision", "task", "general"}:
                category = "general"
            subject = str(m.get("subject", "")).strip() or kw
            scope_type = str(m.get("scope_type", "user")).strip().casefold()
            if scope_type not in {"user", "channel"}:
                scope_type = "user"
            operation = str(m.get("operation", "create")).strip().casefold()
            if operation not in {"create", "replace", "delete"}:
                continue
            if operation == "delete":
                deleted = await repo.forget_memory_slot(
                    user_id, channel_id, category, subject, scope_type,
                )
                if deleted:
                    _invalidate_search_cache(
                        user_id,
                        "" if scope_type == "user" else channel_id,
                        shared_channel=scope_type == "channel",
                    )
                    saved += len(deleted)
                continue
            single_value = (
                bool(m.get("single_value", False))
                or operation == "replace"
                or category == "identity"
                or subject.casefold() in {
                    "nickname", "preferred_name", "language", "timezone",
                    "response_style", "preferred_language",
                }
            )
            try:
                imp = max(1, min(5, int(m.get("importance", 1))))
            except (TypeError, ValueError):
                imp = 1
            normalized = " ".join(cnt.casefold().split())
            content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            await save_memory(
                user_id,
                kw,
                cnt,
                imp,
                channel_id,
                category=category,
                subject=subject,
                scope_type=scope_type,
                confidence=confidence_value,
                source_message_id=source_message_id,
                source_excerpt=excerpt[:300],
                content_hash=content_hash,
                single_value=single_value,
            )
            saved += 1
        if saved:
            logger.debug("[memory_manager] extract user=%s saved=%d", user_id, saved)
    except asyncio.TimeoutError:
        logger.warning(
            "[memory_manager] operation=extract status=timeout user=%s model=%s",
            user_id, _EXTRACT_MODEL,
        )
    except Exception as e:
        if is_quota_error(e):
            retry_after = mark_quota_exhausted_from_error(_EXTRACT_MODEL, e)
            logger.warning(
                "[memory_manager] operation=extract status=429 user=%s model=%s "
                "retry_after=%ds error=%s",
                user_id, _EXTRACT_MODEL, retry_after, e,
            )
        else:
            logger.exception(
                "[memory_manager] operation=extract status=%s user=%s model=%s error=%s",
                error_status_code(e) or "exception", user_id, _EXTRACT_MODEL, e,
            )


async def _summarize_if_needed(user_id: str, channel_id: str = "") -> None:
    count = await repo.count_messages(user_id, channel_id)
    if count < _summary_trigger():
        return
    state = await repo.get_summary_state(user_id, channel_id)
    messages = await repo.get_messages_after(
        user_id,
        channel_id,
        int(state.get("last_message_id", 0)),
        exclude_recent=_summary_keep(),
    )
    required = max(_summary_min_messages(), _summary_new_message_trigger())
    if len(messages) < required:
        return
    line_max_chars = _summary_line_max_chars()
    new_conversation = "\n".join(
        f"{item['role']}: {item['content'][:line_max_chars]}" for item in messages
    )
    previous_summary = str(state.get("summary", "")).strip()
    conversation = (
        f"<previous_summary>\n{previous_summary}\n</previous_summary>\n\n"
        f"<new_messages>\n{new_conversation}\n</new_messages>"
    )
    try:
        async with background_request(_SUMMARY_MODEL) as allowed:
            if not allowed:
                logger.debug("[memory_manager] summary deferred user=%s", user_id)
                return
            res = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model    = _SUMMARY_MODEL,
                    contents = conversation,
                    config   = types.GenerateContentConfig(
                        system_instruction=_SUMMARY_SYSTEM,
                    ),
                ),
                timeout=_summary_timeout(),
            )
        summary = (res.text or "").strip()
        if summary:
            last_message_id = max(int(item["id"]) for item in messages)
            await repo.upsert_summary(
                user_id, summary, count, channel_id, last_message_id,
            )
            _invalidate_search_cache(user_id, channel_id)
            logger.info(
                "[memory_manager] summary user=%s msg=%d len=%d",
                user_id, count, len(summary),
            )
    except asyncio.TimeoutError:
        logger.warning(
            "[memory_manager] operation=summary status=timeout user=%s model=%s",
            user_id, _SUMMARY_MODEL,
        )
    except Exception as e:
        if is_quota_error(e):
            retry_after = mark_quota_exhausted_from_error(_SUMMARY_MODEL, e)
            logger.warning(
                "[memory_manager] operation=summary status=429 user=%s model=%s "
                "retry_after=%ds error=%s",
                user_id, _SUMMARY_MODEL, retry_after, e,
            )
        else:
            logger.exception(
                "[memory_manager] operation=summary status=%s user=%s model=%s error=%s",
                error_status_code(e) or "exception", user_id, _SUMMARY_MODEL, e,
            )


async def _vectorize_recent(user_id: str, channel_id: str) -> None:
    """只向量化尚未有相同 content_hash 的新／已更新記憶。"""
    await asyncio.sleep(_vectorize_delay_seconds())
    mems = await repo.get_memories_needing_vectors(
        user_id,
        channel_id,
        limit=_vector_candidate_limit(),
        embedding_model=_EMBED_MODEL,
    )
    for item in mems:
        kw = item["keyword"]
        content = item["content"]
        imp = item["importance"]
        vec = await _embed(f"{kw}: {content}", background=True)
        if vec:
            owner_user_id = str(item["user_id"])
            vector_channel_id = (
                "" if item["scope_type"] == "user" else str(item["channel_id"])
            )
            await repo.upsert_vector(
                owner_user_id,
                kw,
                content,
                vec,
                imp,
                vector_channel_id,
                memory_id=int(item["id"]),
                content_hash=str(item["content_hash"]),
                embedding_model=_EMBED_MODEL,
            )


async def force_summarize(user_id: str) -> str:
    """強制生成摘要（供管理指令使用）。"""
    messages = await repo.get_messages_excluding_recent(user_id, _summary_keep())
    if not messages:
        return ""
    line_max_chars = _summary_line_max_chars()
    conversation = "\n".join(
        f"{role}: {content[:line_max_chars]}" for role, content in messages
    )
    try:
        async with foreground_request():
            res = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model    = _SUMMARY_MODEL,
                    contents = conversation,
                    config   = types.GenerateContentConfig(
                        system_instruction=_SUMMARY_SYSTEM,
                    ),
                ),
                timeout=_summary_timeout(),
            )
        summary = (res.text or "").strip()
        if summary:
            await repo.upsert_summary(user_id, summary, len(messages))
        return summary
    except Exception as e:
        if is_quota_error(e):
            retry_after = mark_quota_exhausted_from_error(_SUMMARY_MODEL, e)
            logger.warning(
                "[memory_manager] operation=force_summary status=429 model=%s "
                "retry_after=%ds error=%s",
                _SUMMARY_MODEL, retry_after, e,
            )
        else:
            logger.exception(
                "[memory_manager] operation=force_summary status=%s model=%s error=%s",
                error_status_code(e) or "exception", _SUMMARY_MODEL, e,
            )
        return ""

# ── 數學工具 ──────────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


async def _embed(text: str, *, background: bool = False) -> list[float] | None:
    try:
        if background:
            async with background_request(_EMBED_MODEL) as allowed:
                if not allowed:
                    return None
                res = await _call_embed(text)
        else:
            async with foreground_request():
                res = await _call_embed(text)
        embeddings = getattr(res, "embeddings", None)
        if embeddings and embeddings[0].values:
            return list(embeddings[0].values)
    except asyncio.TimeoutError:
        logger.warning(
            "[memory_manager] operation=embed status=timeout model=%s", _EMBED_MODEL,
        )
    except Exception as e:
        if is_quota_error(e):
            retry_after = mark_quota_exhausted_from_error(_EMBED_MODEL, e)
            logger.warning(
                "[memory_manager] operation=embed status=429 model=%s "
                "retry_after=%ds error=%s",
                _EMBED_MODEL, retry_after, e,
            )
        else:
            logger.exception(
                "[memory_manager] operation=embed status=%s model=%s error=%s",
                error_status_code(e) or "exception", _EMBED_MODEL, e,
            )
    return None


async def _call_embed(text: str):
    return await asyncio.wait_for(
        client.aio.models.embed_content(
            model=_EMBED_MODEL,
            contents=text[:_embedding_max_chars()],
        ),
        timeout=_embed_timeout(),
    )

# ── 事件注冊 ──────────────────────

event_bus.on("message_generated", _on_message_generated)
