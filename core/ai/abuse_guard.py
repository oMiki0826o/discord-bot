"""
core/ai/abuse_guard.py

Modification():

- 使用 get_int() 讀取視窗、門檻與限制分鐘數，避免設定轉型錯誤中斷請求。
- 單次檢查只讀取一次設定值，降低重複查詢與 log 雜訊。
- 保留滑動視窗與持久化暫時限制流程。

Description():

- 本檔以記憶體滑動視窗追蹤每位使用者的 AI 請求頻率。
- 超過門檻時會寫入 temp_restriction，並透過既有 ERROR log 管道通知 Owner。

設計說明：
- 滑動視窗狀態保存在記憶體，Bot 重啟後自動清空，不需要持久化；
  暫時限制本身（temp_restrictions 表）才需要持久化，
  因為限制要跨重啟維持到 expires_at
- 觸發限制時：
    1. 寫入 database.repository.user_repository.set_temp_restriction()
    2. logger.error() 一次 —— core.logging.discord_error_handler 會
       攔截 ERROR 等級的 log 並即時私訊 Owner，沿用既有的錯誤通報
       管道，不需另外建立通知機制
    3. 透過 core.ai.admin_service.log_admin_action() 寫入 audit_log，
       actor_id 標記為 "system" 以區分「自動觸發」與「Owner 手動操作」
- 所有閾值（視窗長度、最大請求數、限制時長）由 settings.json 統一提供，可熱更新
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

import database.repository.user_repository as repo
from core.system.settings import get_int

logger = logging.getLogger("bot.abuse_guard")

# ── 全域狀態（記憶體，重啟自動清空） ──────────────────────

_request_log: dict[str, deque[float]] = defaultdict(deque)

# ── 對外入口 ──────────────────────

def check_and_record(user_id: str) -> tuple[bool, str | None]:
    """
    記錄本次請求並檢查是否允許繼續。

    回傳 (allowed, reason)：
    - allowed=False, reason=既有限制原因 → 使用者目前仍在暫時限制中
    - allowed=False, reason="新觸發限制訊息" → 本次請求觸發了新的限制
    - allowed=True,  reason=None → 正常放行
    """
    now = time.monotonic()
    wall_now = time.time()
    window_seconds = max(1, get_int("ai.abuse_window_seconds", 60))
    max_requests = max(1, get_int("ai.abuse_max_requests", 15))
    restrict_minutes = max(1, get_int("ai.abuse_restrict_minutes", 10))

    # ── 1. 已有未過期的暫時限制 → 直接拒絕，不重複寫入 ──────────────────────
    existing = repo.get_temp_restriction(user_id)
    if existing and existing["expires_at"] > wall_now:
        remaining_min = int((existing["expires_at"] - wall_now) / 60) + 1
        return False, f"請求過於頻繁，暫時限制中，約 {remaining_min} 分鐘後解除"

    # ── 2. 滑動視窗計數 ──────────────────────
    window = _request_log[user_id]
    window.append(now)
    while window and now - window[0] > window_seconds:
        window.popleft()

    if len(window) <= max_requests:
        return True, None

    # ── 3. 超過門檻 → 觸發新的暫時限制 ──────────────────────
    expires_at = wall_now + restrict_minutes * 60
    reason = f"{window_seconds} 秒內請求 {len(window)} 次，超過門檻 {max_requests}"
    repo.set_temp_restriction(user_id, reason, expires_at)
    window.clear()

    logger.error(
        "[abuse_guard] 偵測到異常請求頻率 | user=%s | %s | 限制 %d 分鐘",
        user_id, reason, restrict_minutes,
    )

    try:
        from core.ai.admin_service import log_admin_action
        log_admin_action(
            actor_id="system", command="abuse_guard.auto_restrict",
            target_id=user_id, detail=reason,
        )
    except Exception as e:
        logger.debug("[abuse_guard] audit log 寫入失敗: %s", e)

    return False, f"偵測到異常請求頻率，已暫時限制 {restrict_minutes} 分鐘"


def is_restricted(user_id: str) -> bool:
    """單純檢查目前是否仍在暫時限制中（不計入滑動視窗）。"""
    existing = repo.get_temp_restriction(user_id)
    return bool(existing and existing["expires_at"] > time.time())


def clear_restriction(user_id: str) -> None:
    """供 Owner 手動解除暫時限制（例如誤判時）。"""
    repo.clear_temp_restriction(user_id)
    logger.info("[abuse_guard] 已手動解除暫時限制 | user=%s", user_id)
