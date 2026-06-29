"""
core/ai/admin_service.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

職責：
- 提供 Dashboard 和 Info 指令所需的統計資料
- 作為服務層（Service Layer），dashboard.py 不再直接操作 SQLite
- 所有查詢委派給 budget、memory_manager、user_context、search_manager

設計說明：
- Dashboard 指令只呼叫此模組，不 import database / repository
- 統計資料做一次聚合，避免 cog 內散落多個 DB 查詢

修正：
- 移除與 core.ai.budget 重複的 _count_memories / _count_users，
  改呼叫 budget.get_total_memory_count() / get_total_user_count()
  （SQL 邏輯完全相同，集中於 budget.py 單一處維護）

新增（管理指令 Audit Log）：
- 訂閱 event_bus 的 "admin_action" 事件，寫入 audit_log 表
  （實際存取委派 database.repository.audit_repository）
- 各管理指令（cogs/ai/ai_owner_commands.py、cogs/ai/dashboard.py）
  在操作成功後 emit 這個事件即可被記錄，不需各自寫 SQL
- 新增 get_audit_log() 供 $dashboard audit 顯示最近紀錄
"""

from __future__ import annotations

import asyncio
import logging
import time

import database.repository.audit_repository as audit_repo
import database.repository.memory_repository as mem_repo
import database.repository.user_repository as user_repo
from core.ai.budget import (
    get_global_stats,
    get_top_users,
    get_total_memory_count,
    get_total_user_count,
    get_user_stats,
)
from core.ai.content_guard import get_rules_text
from core.ai.content_guard import reload_rules as _reload_moderation_rules
from core.ai.memory_manager import force_summarize
from core.ai.prompt_builder import deactivate, delete_template, list_templates, set_active
from core.ai.search_manager import cleanup_expired
from core.ai.search_manager import get_stats as get_cache_stats
from core.ai.user_context import STATE_LABELS, dump_social
from core.system import event_bus

logger = logging.getLogger("bot.admin_service")


# ── 全系統統計 ──────────────────────

def get_dashboard_data(bot) -> dict:
    """
    組合 Dashboard embed 所需的所有資料。
    bot 物件用於取得 guilds 數量和延遲。
    """
    stats        = get_global_stats(hours=24)
    cache        = get_cache_stats()
    mem_count    = get_total_memory_count()
    vec_count    = mem_repo.count_vectors()
    summary_count= mem_repo.count_summaries()
    user_count   = get_total_user_count()

    return {
        "requests_24h":   stats["total_requests"],
        "tokens_24h":     stats["total_tokens"],
        "active_users":   stats["active_users"],
        "error_count":    stats["error_count"],
        "error_rate":     stats["error_rate"],
        "cache_hits":     stats["cache_hits"],
        "cache_valid":    cache["valid"],
        "memory_count":   mem_count,
        "vector_count":   vec_count,
        "summary_count":  summary_count,
        "user_count":     user_count,
        "guild_count":    len(bot.guilds),
        "latency_ms":     bot.latency * 1000,
        "by_model":       stats["by_model"],
        "estimated_ratio": stats["estimated_ratio"],
    }


def get_user_data(user_id: str) -> dict:
    """使用者 30 天統計，供 $info @使用者 使用。"""
    return get_user_stats(user_id, days=30)


def get_token_leaderboard(limit: int = 10, days: int = 30) -> list[dict]:
    """Token 用量排行榜，供 $dashboard user 使用。"""
    return get_top_users(limit=limit, days=days)


def get_global_summary(hours: int = 24) -> dict:
    """全系統統計，供 $info 使用。"""
    return get_global_stats(hours=hours)


# ── 快取管理 ──────────────────────

def do_cache_cleanup() -> tuple[dict, int, dict]:
    """
    執行一次快取清理。
    回傳 (清理前統計, 刪除筆數, 清理後統計)。
    """
    before  = get_cache_stats()
    removed = cleanup_expired()
    after   = get_cache_stats()
    return before, removed, after


# ── 狀態管理 ──────────────────────

def list_active_states() -> list[dict]:
    """
    取得所有非 normal 的有效狀態。
    回傳 [{"user_id", "state", "label", "expires_at"}, ...]
    """
    now  = time.time()
    rows = user_repo.list_active_states(now)
    return [
        {
            "user_id":    r["user_id"],
            "state":      r["state"],
            "label":      STATE_LABELS.get(r["state"], r["state"]),
            "expires_at": r["expires_at"],
        }
        for r in rows
    ]


# ── 社交資料 ──────────────────────

def get_social_dump() -> dict:
    """供 $社交 指令展示，取代舊版 social._load()。"""
    return dump_social()


# ── 模板管理（委派 prompt_builder） ──────────────────────

def get_templates() -> list[dict]:
    return list_templates()


def activate_template(name: str) -> bool:
    return set_active(name)


def remove_template(name: str) -> bool:
    return delete_template(name)


def deactivate_template() -> None:
    deactivate()


# ── 摘要 ──────────────────────

async def run_force_summarize(user_id: str) -> str:
    return await force_summarize(user_id)


# ── Audit Log ──────────────────────

async def _on_admin_action(
    actor_id:  str,
    command:   str,
    target_id: str = "",
    detail:    str = "",
    **_,
) -> None:
    """event_bus 觸發：寫入一筆管理指令操作紀錄。"""
    audit_repo.insert_log(actor_id, command, target_id, detail)


def log_admin_action(
    actor_id:  str,
    command:   str,
    target_id: str = "",
    detail:    str = "",
) -> None:
    """
    供各管理指令呼叫，觸發 audit log 寫入事件。

    使用 event_bus.emit 而非直接寫 DB，理由與
    core.ai.core 觸發 "message_generated" 一致：
    背景任務以 create_task 執行，不阻塞指令回應。

    修正（單元測試發現）：原本直接呼叫 asyncio.create_task(coro)，
    若呼叫當下沒有執行中的 event loop（例如非 async context），
    create_task() 會拋出 RuntimeError，但此時 coro（呼叫
    event_bus.emit() 產生的 coroutine 物件）已經建立完畢，
    永遠不會被 await，造成 "coroutine was never awaited" 警告。
    改為先檢查是否有執行中的 event loop，沒有時明確關閉該
    coroutine 物件並記錄一筆 debug log，而不是讓例外處理留下
    孤兒 coroutine。
    """
    coro = event_bus.emit(
        "admin_action",
        actor_id=actor_id, command=command,
        target_id=target_id, detail=detail,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        logger.debug(
            "[admin_service] log_admin_action 在無 event loop 環境下被呼叫，已略過: %s",
            command,
        )
        return
    asyncio.create_task(coro)


def get_audit_log(limit: int = 20) -> list[dict]:
    """取得最近 N 筆管理指令操作紀錄，供 $dashboard audit 使用。"""
    return audit_repo.get_recent(limit)


event_bus.on("admin_action", _on_admin_action)


# ── 內容審核規則 ──────────────────────

def get_moderation_rules() -> str:
    """取得目前生效的內容審核規則原文，供 $dashboard rules 顯示。"""
    return get_rules_text()


def reload_rules() -> str:
    """強制重新讀取 moderation_rules.txt，供 $dashboard rules reload 使用。"""
    return _reload_moderation_rules()
