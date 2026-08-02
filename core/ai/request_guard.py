"""
core/ai/request_guard.py

Modification():

- 新增本檔案：從 cogs/ai/chat.py 抽出每位使用者的並發鎖與冷卻邏輯。
  原本 `_user_locks` / `_user_cooldown` 是 Chat Cog 的實例屬性，只
  在「被 @mention 觸發」這一條路徑上生效。新增 /ai 這個 slash 指令
  後，若冷卻狀態各自獨立存放在不同 Cog 實例裡，使用者只要交替用
  @mention 和 /ai 兩種方式呼叫，就能繞過原本設計的冷卻與並發限制
  （例如冷卻中改用 /ai 立刻再發一次）。改為模組層級的共用狀態後，
  不論從哪個入口呼叫 AI，都會查詢與更新同一份紀錄，節流才有實際
  意義。

職責：
- 依 user_id 提供專屬 asyncio.Lock，避免同一使用者同時觸發多個並行
  AI 請求。
- 以 monotonic clock 追蹤每位使用者的最後請求時間，實作簡單冷卻。
- 冷卻秒數與提示文案讀取 settings.json，可熱更新。
"""

from __future__ import annotations

import asyncio

from core.system.settings import get_float, get_str

# ── 全域狀態（模組層級，跨 Cog 共用；Bot 重啟後自動清空） ──────────────────────

_user_locks:    dict[int, asyncio.Lock] = {}
_user_cooldown: dict[int, float]        = {}


# ── 並發鎖 ──────────────────────

def lock_for(user_id: int) -> asyncio.Lock:
    """取得使用者專屬鎖，避免同一使用者同時觸發多個 AI 請求。"""
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


# ── 冷卻檢查 ──────────────────────

def check_cooldown(user_id: int) -> bool:
    """
    回傳 True 表示通過冷卻、可以繼續；False 表示還在冷卻中。
    同時更新最後請求時間戳。
    使用 monotonic clock，不受系統時鐘調整影響。
    """
    cooldown_seconds = max(0.0, get_float("ai.cooldown_seconds", 3.0))
    now = asyncio.get_running_loop().time()
    last = _user_cooldown.get(user_id, 0.0)
    if now - last < cooldown_seconds:
        return False
    _user_cooldown[user_id] = now
    return True


def cooldown_message() -> str:
    """依設定檔產生冷卻提示。"""
    seconds  = max(0.0, get_float("ai.cooldown_seconds", 3.0))
    template = get_str("ai.cooldown_message_template", "請稍等 {seconds:g} 秒再試")
    try:
        return template.format(seconds=seconds)
    except (KeyError, ValueError):
        return f"請稍等 {seconds:g} 秒再試"
