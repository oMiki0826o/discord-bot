"""
core/ai/tool_registry.py

Modification():
- 修正 _exec_memory() 呼叫 memory_manager.search() 時的參數錯位 bug：
  原本 search(user_id, query, get_global_memories()) 只給 3 個位置
  參數，但實際簽名是 (user_id, channel_id, query, global_mems) 共 4
  個，導致 channel_id 被填成問題文字、問題文字被填成全域記憶清單、
  global_mems 完全沒傳入。已改為 search(user_id, channel_id, query,
  get_global_memories())。連帶將 ExecutorFn 型別與 _exec_summary /
  _exec_profile 的參數統一補上 channel_id，讓三個 executor 維持
  相同簽名（即使後兩者目前用不到 channel_id）。
- 統一檔案註解格式，保留原有職責說明。

修正（工具可插拔架構）：
- 新增 ToolEntry 結構與 TOOL_REGISTRY 註冊表（tuple），
  風格與 core/system/startup_registry.py 的 WarmupEntry / REGISTRY 完全對齊
- agent_router._select_tools() 改為單純「遍歷 TOOL_REGISTRY，呼叫每個工具的
  判斷函式，收集符合條件的工具名稱」，本身不再包含任何工具專屬邏輯
- 新增工具只需在此追加一個 ToolEntry，不需修改 agent_router.py
  與 context_manager.py，符合「不動核心邏輯」的可插拔性目標
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger("bot.tool_registry")

# ── 型別定義 ──────────────────────

# trigger：接收 (prompt, route_context)，回傳是否啟用此工具
TriggerFn  = Callable[[str, dict], bool]
# executor：實際執行工具邏輯，參數為 (user_id, channel_id, query)，
# 回傳要插入 prompt 的文字片段。
ExecutorFn = Callable[[str, str, str], Awaitable[str]]


@dataclass(frozen=True)
class ToolEntry:
    """
    單一工具的註冊項目。

    name      ：工具識別名稱（如 "memory"、"summary"、"profile"）
    trigger   ：判斷此次請求是否要啟用此工具
    executor  ：實際執行邏輯，參數為 (user_id, channel_id, query)，
                回傳 prompt 片段
    priority  ：數字越小越優先，決定多個工具同時觸發時的組裝順序
    """
    name:     str
    trigger:  TriggerFn
    executor: ExecutorFn
    priority: int = 100


# ── 各工具的觸發規則 ──────────────────────
# 規則邏輯從 agent_router.py 搬移至此，行為完全不變，純粹分離「規則」與「分派」。

_MEMORY_KEYWORDS: tuple[str, ...] = (
    "記得", "之前", "上次", "曾經", "你說", "我說",
    "remember", "before", "last time", "you said",
)

_SUMMARY_KEYWORDS: tuple[str, ...] = (
    "剛才", "剛剛", "前面", "之前說", "我們", "話題",
    "earlier", "before", "we talked", "conversation",
)

_PROFILE_KEYWORDS: tuple[str, ...] = (
    "我喜歡", "我習慣", "我偏好", "推薦", "適合我",
    "i like", "i prefer", "suggest", "recommend for me",
)


def _trigger_memory(prompt: str, ctx: dict) -> bool:
    """太短的打招呼不啟用；超過 10 字或含記憶關鍵字才啟用。"""
    if len(prompt) < 8:
        return False
    p = prompt.lower()
    return len(prompt) > 10 or any(k in p for k in _MEMORY_KEYWORDS)


def _trigger_summary(prompt: str, ctx: dict) -> bool:
    if len(prompt) < 8:
        return False
    p = prompt.lower()
    return any(k in p for k in _SUMMARY_KEYWORDS)


def _trigger_profile(prompt: str, ctx: dict) -> bool:
    if len(prompt) < 8:
        return False
    p = prompt.lower()
    return any(k in p for k in _PROFILE_KEYWORDS)


# ── 各工具的執行邏輯 ──────────────────────
# 內部 import 避免循環依賴（與原 agent_router.execute_tools 行為一致）。

async def _exec_memory(user_id: str, channel_id: str, query: str) -> str:
    """
    修正：原本呼叫 search(user_id, query, get_global_memories())，
    但 memory_manager.search() 實際簽名是
    (user_id, channel_id, query, global_mems)——三個位置參數對應到
    四個參數，等於把 query 誤塞進 channel_id、把 get_global_memories()
    回傳的清單誤塞進 query，global_mems 反而完全沒有傳入。由於
    select_tools() 的觸發條件相當寬鬆（訊息超過 10 字就會觸發，見
    _trigger_memory），這個錯位在大多數對話中都會發生，且一旦這裡
    傳回非空內容，prompt_builder 就會跳過 context_manager 那條正確的
    記憶路徑（見 prompt_builder.py 的「若 Tool 已注入相關記憶則跳過」
    邏輯），等於用參數錯位、幾乎沒在比對正確頻道與問題的結果，蓋掉了
    原本正確的記憶內容。
    """
    try:
        from core.ai.memory_manager import search
        from core.ai.user_context import get_global_memories
        bundle = search(user_id, channel_id, query, await get_global_memories())
        if bundle.memories:
            lines = [f"- [{kw}] '{c}'" for kw, c, _ in bundle.memories[:5]]
            return "=== 工具：相關記憶 ===\n" + "\n".join(lines)
    except Exception as e:
        logger.debug("[tool_registry] memory executor error: %s", e)
    return ""


async def _exec_summary(user_id: str, channel_id: str, query: str) -> str:
    """channel_id / query 目前用不到，僅為符合共用的 ExecutorFn 簽名而保留。"""
    try:
        from core.ai.memory_manager import get_summary_text
        s = await get_summary_text(user_id)
        return f"=== 工具：對話摘要 ===\n{s}" if s else ""
    except Exception as e:
        logger.debug("[tool_registry] summary executor error: %s", e)
    return ""


async def _exec_profile(user_id: str, channel_id: str, query: str) -> str:
    """channel_id / query 目前用不到，僅為符合共用的 ExecutorFn 簽名而保留。"""
    try:
        from core.ai.user_context import profile_to_prompt
        return await profile_to_prompt(user_id)
    except Exception as e:
        logger.debug("[tool_registry] profile executor error: %s", e)
    return ""


# ── 工具註冊表（新增工具只需在此追加一行） ──────────────────────

TOOL_REGISTRY: tuple[ToolEntry, ...] = (
    ToolEntry(name="memory",  trigger=_trigger_memory,  executor=_exec_memory,  priority=10),
    ToolEntry(name="summary", trigger=_trigger_summary, executor=_exec_summary, priority=20),
    ToolEntry(name="profile", trigger=_trigger_profile, executor=_exec_profile, priority=30),
)


# ── 對外查詢介面 ──────────────────────

def select_tools(prompt: str, route_context: dict | None = None) -> list[str]:
    """
    遍歷 TOOL_REGISTRY，呼叫每個工具的 trigger，收集符合條件的工具名稱。
    依 priority 排序，本函式不包含任何工具專屬邏輯。
    """
    ctx = route_context or {}
    matched = [e for e in TOOL_REGISTRY if e.trigger(prompt, ctx)]
    matched.sort(key=lambda e: e.priority)
    return [e.name for e in matched]


def get_executor(name: str) -> ExecutorFn | None:
    """依工具名稱查詢對應 executor，找不到回傳 None。"""
    for entry in TOOL_REGISTRY:
        if entry.name == name:
            return entry.executor
    return None
