"""
core/ai/user_context.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

修正（重構）：
- 修正 from core import event_bus → from core.ai import event_bus（正確路徑）
- 移除 google.genai 以外的無用 import
- 精簡 dump_social() 中的 DB 存取方式
- 統一資料結構與函式命名格式
- _PROFILE_MODEL 改由 core.ai.models 統一提供，移除硬編碼字串
- 修正 update_profile_from_interaction() 的 JSON fence 清理錯誤：
    原 raw.lstrip("```json").lstrip("```").rstrip("```")
    為「移除字元集合」而非「移除前綴字串」，會誤刪 JSON 開頭的
    j / s / o / n 等字元；改用 core.ai.json_utils.strip_json_fence()
    （與 memory_manager.py 共用同一套正則清理邏輯）
- client 改由 core.ai.gemini_client 統一提供，移除本檔的
  genai.Client(api_key=GEMINI_API) 重複建立，並連帶移除
  未使用的 GEMINI_API import
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from google.genai import types

import database.repository.user_repository as repo
from core.ai.gemini_client import client
from core.ai.json_utils import strip_json_fence
from core.ai.models import MODELS

# ── import 路徑修正（原路徑 from core import event_bus 為錯誤路徑） ──────────────────────
from core.system import event_bus

logger = logging.getLogger("bot.user_context")

# ── 常數 ──────────────────────
TIER_NAMES: dict[int, str] = {
    0: "陌生人",
    1: "路人",
    2: "朋友",
    3: "開拓者",
}

STATE_LABELS: dict[str, str] = {
    "normal":   "一般對話",
    "roleplay": "角色扮演",
    "creative": "創意寫作",
    "task":     "任務模式",
    "debate":   "辯論模式",
}

_DEFAULT_TTL_MIN = 60
_PROFILE_MODEL   = MODELS["lite"]
_PROFILE_TIMEOUT = 10
_PROFILE_SYSTEM  = (
    "你是使用者偏好分析器。根據對話推斷使用者的偏好，只輸出 JSON，"
    "無法判斷的欄位省略。格式：\n"
    '{"topics":["話題"],"style":"正式/輕鬆/幽默","lang":"zh_tw","notes":"其他"}'
)


# ── 資料結構 ──────────────────────

@dataclass
class UserInfo:
    user_id:           str
    username:          str
    tier:              int
    tier_name:         str
    interaction_count: int
    state:             str  = "normal"
    state_label:       str  = "一般對話"
    profile:           dict = field(default_factory=dict)


# ── 等級 ──────────────────────

def get_tier(user_id: str) -> int:
    return repo.get_tier(user_id)


def set_tier(user_id: str, tier: int) -> None:
    tier = max(0, min(3, tier))
    repo.set_tier(user_id, tier)
    logger.info("[tier] user=%s → %s", user_id, TIER_NAMES[tier])


def get_tier_name(tier: int) -> str:
    return TIER_NAMES.get(tier, "陌生人")


# ── 封鎖 ──────────────────────

def is_banned(user_id: str) -> bool:
    return repo.is_banned(user_id)


def ban_user(user_id: str, reason: str = "") -> None:
    repo.ban(user_id, reason)
    logger.info("[ban] user=%s reason=%s", user_id, reason or "無說明")


def unban_user(user_id: str) -> None:
    repo.unban(user_id)
    logger.info("[unban] user=%s", user_id)


# ── 互動計數 ──────────────────────

def get_interaction_count(user_id: str) -> int:
    return repo.get_interaction_count(user_id)


def increment_interaction(user_id: str) -> int:
    return repo.increment_interaction(user_id)


# ── 使用者資訊（統一入口） ──────────────────────

def get_user_info(user_id: str, username: str = "") -> dict:
    """供 build_prompt 使用的扁平 dict（向下相容舊呼叫方式）。"""
    tier  = get_tier(user_id)
    count = get_interaction_count(user_id)
    return {
        "user_id":           user_id,
        "username":          username,
        "tier":              tier,
        "tier_name":         get_tier_name(tier),
        "interaction_count": count,
    }


def get_user_context(user_id: str, username: str = "") -> UserInfo:
    """完整使用者上下文，供 context_manager 使用。"""
    tier    = get_tier(user_id)
    count   = get_interaction_count(user_id)
    state_d = get_state(user_id)
    profile = get_profile(user_id)
    return UserInfo(
        user_id           = user_id,
        username          = username,
        tier              = tier,
        tier_name         = get_tier_name(tier),
        interaction_count = count,
        state             = state_d["state"],
        state_label       = state_d["label"],
        profile           = profile,
    )


# ── 全域記憶 ──────────────────────

def get_global_memories() -> list[tuple[str, str, int]]:
    return repo.list_global_memories()


def set_global_memory(keyword: str, content: str, importance: int = 5) -> None:
    importance = max(1, min(5, importance))
    repo.upsert_global_memory(keyword, content, importance)
    logger.info("[global_memory] upsert keyword=%s", keyword)


def remove_global_memory(keyword: str) -> bool:
    ok = repo.delete_global_memory(keyword)
    if ok:
        logger.info("[global_memory] removed keyword=%s", keyword)
    return ok


# ── 對話狀態 ──────────────────────

def get_state(user_id: str) -> dict:
    """取得當前狀態，過期時回傳預設 normal。"""
    row = repo.get_state_row(user_id)
    if not row:
        return _default_state()
    if row["expires_at"] and row["expires_at"] < time.time():
        return _default_state()
    return {
        "state":   row["state"],
        "context": row["context"],
        "label":   STATE_LABELS.get(row["state"], row["state"]),
    }


def set_state(
    user_id:     str,
    state:       str,
    context:     dict | None = None,
    ttl_minutes: int = _DEFAULT_TTL_MIN,
) -> None:
    context    = context or {}
    expires_at = time.time() + ttl_minutes * 60 if ttl_minutes > 0 else None
    repo.upsert_state(user_id, state, context, expires_at)
    logger.info("[state] user=%s → %s (ttl=%dm)", user_id, state, ttl_minutes)


def extend_state(user_id: str, ttl_minutes: int = _DEFAULT_TTL_MIN) -> None:
    """每次互動延長 TTL（滑動視窗）。normal 狀態不延長。"""
    current = get_state(user_id)
    if current["state"] == "normal":
        return
    set_state(user_id, current["state"], current["context"], ttl_minutes)


def clear_state(user_id: str) -> None:
    repo.delete_state(user_id)
    logger.info("[state] user=%s cleared", user_id)


def state_to_prompt(user_id: str) -> str:
    """normal 或無 context 時回傳空字串。"""
    s = get_state(user_id)
    if s["state"] == "normal" and not s["context"]:
        return ""
    lines = ["=== 當前狀態 ===", f"狀態：{s['label']}"]
    for k, v in list(s["context"].items())[:3]:
        lines.append(f"{k}：{v}")
    return "\n".join(lines)


def _default_state() -> dict:
    return {"state": "normal", "context": {}, "label": "一般對話"}


# ── 個人檔案 ──────────────────────

def get_profile(user_id: str) -> dict:
    return repo.get_profile(user_id)


def update_profile(user_id: str, key: str, value) -> None:
    """手動更新 profile 單一欄位。"""
    profile      = get_profile(user_id)
    profile[key] = value
    repo.save_profile(user_id, "", profile)


def profile_to_prompt(user_id: str) -> str:
    """profile 為空時回傳空字串。"""
    profile = get_profile(user_id)
    if not profile:
        return ""
    lines = []
    if topics := profile.get("topics"):
        lines.append(f"- 常見話題：{', '.join(topics[:5])}")
    if style := profile.get("style"):
        lines.append(f"- 溝通風格：{style}")
    if notes := profile.get("notes"):
        lines.append(f"- 備註：{notes}")
    return ("=== 使用者偏好 ===\n" + "\n".join(lines)) if lines else ""


# ── AI 背景更新 Profile ──────────────────────

async def update_profile_from_interaction(
    user_id:  str,
    username: str,
    user_msg: str,
    ai_msg:   str,
) -> None:
    """
    背景執行：AI 分析對話更新 profile。
    由 event_bus 觸發，任何例外靜默處理。
    """
    try:
        res = await asyncio.wait_for(
            client.aio.models.generate_content(
                model    = _PROFILE_MODEL,
                contents = f"User: {user_msg[:500]}\nAI: {ai_msg[:500]}",
                config   = types.GenerateContentConfig(
                    system_instruction=_PROFILE_SYSTEM,
                ),
            ),
            timeout=_PROFILE_TIMEOUT,
        )
        raw = (res.text or "").strip()
        raw = strip_json_fence(raw)
        if not raw:
            return

        updates = json.loads(raw)
        if not isinstance(updates, dict) or not updates:
            return

        existing = get_profile(user_id)
        for k, v in updates.items():
            if k == "topics" and isinstance(v, list):
                old = existing.get("topics", [])
                existing["topics"] = list(dict.fromkeys(old + v))[:10]
            else:
                existing[k] = v

        repo.save_profile(user_id, username, existing)
        logger.debug(
            "[user_context] profile updated user=%s fields=%s",
            user_id, list(updates),
        )
    except asyncio.TimeoutError:
        logger.debug("[user_context] profile timeout user=%s", user_id)
    except Exception as e:
        logger.debug("[user_context] profile error user=%s: %s", user_id, e)


# ── 展示資料（供 $社交 指令使用） ──────────────────────

def dump_social() -> dict:
    """
    回傳目前全部社交資料的快照，取代舊版 social._load()。
    格式與舊版 JSON 結構相容，讓 owner.py 無需大改。
    """
    from database.ai.sqlite import get_connection

    conn = get_connection()
    c    = conn.cursor()

    c.execute("SELECT user_id, tier FROM user_tiers")
    tiers = {r["user_id"]: r["tier"] for r in c.fetchall()}

    c.execute("SELECT user_id, reason FROM user_bans")
    bans = {r["user_id"]: r["reason"] for r in c.fetchall()}

    c.execute("SELECT user_id, count FROM user_interactions")
    interactions = {r["user_id"]: r["count"] for r in c.fetchall()}

    conn.close()

    global_mems = [
        {"keyword": kw, "content": content, "importance": imp}
        for kw, content, imp in get_global_memories()
    ]

    return {
        "tiers":           tiers,
        "bans":            bans,
        "interactions":    interactions,
        "global_memories": global_mems,
    }


# ── 事件監聽 ──────────────────────

async def _on_message_generated(**kw) -> None:
    await update_profile_from_interaction(
        kw["user_id"],
        kw.get("username", ""),
        kw["user_msg"],
        kw["ai_msg"],
    )

event_bus.on("message_generated", _on_message_generated)
