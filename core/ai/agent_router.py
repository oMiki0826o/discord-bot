"""
core/ai/agent_router.py

Modification():
- execute_tools() 新增 channel_id 參數並傳給各 executor：
  tool_registry._exec_memory() 原本呼叫 memory_manager.search() 時
  少傳 channel_id（詳見 tool_registry.py 的說明），根源在於 executor
  的呼叫鏈（context_manager → execute_tools → executor）從頭到尾
  都沒有 channel_id 可用。修正需要沿著呼叫鏈往上補，本檔案是其中
  一環：呼叫端 context_manager._get_tools() 已經拿得到 channel_id，
  這裡只需要多接一個參數並原樣往下傳。
- 將工具決策移交 tool_registry，路由器只負責模型與工具清單決策。
- 改用 core.ai.models 的集中模型常數，移除本檔重複硬編碼模型名稱。
- 保留純規則路由，不額外呼叫 AI，降低延遲與費用。
- route() 新增 model_override 參數：供 /ai 指令的下拉選單直接指定
  MODEL_CHOICES 的某個 key，優先權高於原本 prompt 文字內的關鍵字
  （「用flash」等）；「手動指定模型但該模型不支援搜尋」的升級判斷
  抽成 _guard_search_capability()，讓下拉選單覆寫與文字關鍵字覆寫
  共用同一份保護邏輯，不必各寫一次。
- 新增 MODEL_CHOICES：flash／gemini／gemma 三個可手動指定的模型鍵值
  對照表，供 cogs/ai/ai_command.py 的 Discord Choice 選單與本檔的
  _MODEL_OVERRIDES 共用同一份清單，避免兩份清單分開維護後彼此脫節；
  _MODEL_OVERRIDES 現由 MODEL_CHOICES 推導產生，不再重複寫死。

職責：
- 依 prompt（與可選的手動指定模型）決定模型、搜尋需求與工具清單。
- 提供 execute_tools() 並行執行已選工具。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from core.ai.models import (
    GROUNDING_CATEGORY,
    category_for_model,
    get_default_category,
    get_primary_model,
)
from core.ai.tool_registry import get_executor, select_tools

logger = logging.getLogger("bot.agent_router")

# ── 模型選擇用關鍵字表 ──────────────────────

_WEB_KEYWORDS: tuple[str, ...] = (
    "最新", "新聞", "即時", "天氣", "股價", "匯率", "價格",
    "搜尋", "查詢", "找一下", "查一下", "幫我查",
    "search", "find", "look up", "what happened",
    "latest", "current", "update", "weather",
    "http://", "https://",
)

# 手動指定模型時的文字關鍵字前綴（「用flash」「用gemini」…）。抽成
# 常數是因為 _MODEL_OVERRIDES 由此推導產生；未來若要調整觸發語法
# （例如改成「model:flash」），只需要改這一處。
_OVERRIDE_KEYWORD_PREFIX = "用"

# 可手動指定的模型類別：key 是對外（文字關鍵字／Discord 下拉選單）
# 看到的名稱，value 是 settings.json 模型池的類別名稱。
# cogs/ai/ai_command.py 的 Discord Choice 選單與下方 _MODEL_OVERRIDES
# 皆由此表推導，全專案僅此一份，不重複硬編碼。
MODEL_CHOICES: dict[str, str] = {
    "gemini": "gemini",
    "flash":  "flash",
    "gemma":  "gemma",
}

_MODEL_OVERRIDES: tuple[tuple[str, str], ...] = tuple(
    (f"{_OVERRIDE_KEYWORD_PREFIX}{key}", model)
    for key, model in MODEL_CHOICES.items()
)

_PRO_KEYWORDS: tuple[str, ...] = (
    "程式", "code", "coding", "python", "javascript", "typescript",
    "rust", "golang", "c++", "java", "sql", "debug", "除錯",
    "演算法", "algorithm", "數學", "math", "微積分", "統計", "機率",
    "分析", "報告", "論文", "架構", "設計",
)

# ── 資料結構 ──────────────────────

@dataclass
class RouteDecision:
    model:      str
    use_search: bool
    tools:      list[str] = field(default_factory=list)
    category:   str = ""

    def __post_init__(self) -> None:
        if not self.category:
            self.category = category_for_model(self.model)

    def needs(self, tool: str) -> bool:
        return tool in self.tools

# ── 主要路由 ──────────────────────

def route(prompt: str, model_override: str | None = None) -> RouteDecision:
    """
    純規則路由，零 AI API 呼叫。

    決策順序：
    1. 模型選擇（/ai 指令參數 > prompt 文字關鍵字 > 搜尋需求 >
       內容判斷 > 預設）
    2. 工具決策（委派 tool_registry，本函式不含工具邏輯）

    Args:
        prompt:         使用者輸入內容。
        model_override: MODEL_CHOICES 其中一個 key（來自 /ai 指令的
                         下拉選單）；None 表示交由自動規則判斷。
    """
    category, use_search = _select_category(prompt, model_override)
    model                = get_primary_model(category)
    tools                = select_tools(prompt)

    logger.info(
        "[agent_router] category=%s model=%s search=%s tools=%s override=%s",
        category, model, use_search, tools, model_override,
    )
    return RouteDecision(
        model=model, category=category, use_search=use_search, tools=tools,
    )


def needs_web_search(prompt: str) -> bool:
    p = prompt.lower()
    return any(k in p for k in _WEB_KEYWORDS)


def strip_model_prefix(prompt: str) -> str:
    """移除 prompt 開頭的模型選擇語句，保留其他位置的原文。"""
    lowered = prompt.lower()
    for keyword, _category in _MODEL_OVERRIDES:
        if lowered.startswith(keyword):
            return prompt[len(keyword):].lstrip(" \t:：,，")
    return prompt

# ── 模型選擇 ──────────────────────

def _select_category(prompt: str, model_override: str | None) -> tuple[str, bool]:
    """回傳 (category, use_search)。"""
    p          = prompt.lower()
    use_search = needs_web_search(prompt)

    # ── 1. /ai 指令下拉選單明確指定 ──────────────────────
    if model_override is not None:
        category = MODEL_CHOICES.get(model_override)
        if category is None:
            logger.warning(
                "[agent_router] 未知的 model_override=%s，忽略並改用自動判斷",
                model_override,
            )
        else:
            return _guard_search_capability(category, use_search)

    # ── 2. prompt 文字內的關鍵字指定 ──────────────────────
    for keyword, category in _MODEL_OVERRIDES:
        if keyword in p:
            logger.info("[agent_router] keyword_override=%s", category)
            return _guard_search_capability(category, use_search)

    # ── 3. 需要搜尋 → 強制使用支援搜尋的模型 ──────────────────────
    if use_search:
        category = get_default_category()
        if category == "gemma":
            category = GROUNDING_CATEGORY
        return category, True

    # ── 4. 程式 / 數學 / 分析 → Flash ──────────────────────
    if any(kw in p for kw in _PRO_KEYWORDS):
        return "flash", False

    # ── 5. 預設 ──────────────────────
    return get_default_category(), False


def _guard_search_capability(category: str, use_search: bool) -> tuple[str, bool]:
    """
    手動指定模型（不論來自指令參數或文字關鍵字）時的保護檢查：
    若目前 prompt 需要搜尋，但指定 Gemma 類別，
    強制升級為 Flash 類別並記錄警告，避免搜尋請求送到不
    支援搜尋的模型後靜默失敗或得到過期答案。
    """
    if use_search and category == "gemma":
        logger.warning(
            "[agent_router] 指定類別 %s 不支援搜尋，升級為 %s",
            category, GROUNDING_CATEGORY,
        )
        return GROUNDING_CATEGORY, use_search
    return category, use_search

# ── Tool 執行 ──────────────────────

async def execute_tools(
    decision:   RouteDecision,
    user_id:    str,
    channel_id: str,
    query:      str,
) -> list[str]:
    """
    依決策執行工具，回傳 prompt 片段列表。
    各工具獨立 try-except，單一失敗不影響其他；executor 本身已含例外處理，
    此處僅負責並行排程與結果收集，完全不知道各工具的內部邏輯。
    """
    if not decision.tools:
        return []

    tasks = []
    for name in decision.tools:
        executor = get_executor(name)
        if executor is not None:
            tasks.append(executor(user_id, channel_id, query))
        else:
            logger.warning("[agent_router] 未知工具名稱: %s", name)

    if not tasks:
        return []

    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in gathered if isinstance(r, str) and r]
