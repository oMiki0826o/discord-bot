"""
core/ai/agent_router.py

Modification():
- 將工具決策移交 tool_registry，路由器只負責模型與工具清單決策。
- 改用 core.ai.models 的集中模型常數，移除本檔重複硬編碼模型名稱。
- 保留純規則路由，不額外呼叫 AI，降低延遲與費用。

職責：
- 依 prompt 決定模型、搜尋需求與工具清單。
- 提供 execute_tools() 並行執行已選工具。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from core.ai.models import DEFAULT_MODEL, GROUNDING_MIN_MODEL, MODELS, is_gemini
from core.ai.tool_registry import get_executor, select_tools

logger = logging.getLogger("bot.agent_router")

# ── 模型選擇用關鍵字表 ──────────────────────

_WEB_KEYWORDS: tuple[str, ...] = (
    "最新", "新聞", "即時", "現在", "今天", "今日", "近期", "最近",
    "幾點", "天氣", "股價", "匯率", "價格",
    "查", "搜尋", "找一下", "查一下", "幫我查",
    "search", "find", "look up", "what happened",
    "latest", "current", "update", "weather",
    "http://", "https://",
    "哪裡", "地址", "在哪",
)

_MODEL_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("用flash",  MODELS["flash"]),
    ("用gemini", MODELS["lite"]),
    ("用gemma",  MODELS["gemma"]),
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

    def needs(self, tool: str) -> bool:
        return tool in self.tools

# ── 主要路由 ──────────────────────

def route(prompt: str) -> RouteDecision:
    """
    純規則路由，零 AI API 呼叫。

    決策順序：
    1. 模型選擇（使用者指定 > 搜尋需求 > 內容判斷 > 預設）
    2. 工具決策（委派 tool_registry，本函式不含工具邏輯）
    """
    model, use_search = _select_model(prompt)
    tools             = select_tools(prompt)

    logger.info(
        "[agent_router] model=%s search=%s tools=%s",
        model, use_search, tools,
    )
    return RouteDecision(model=model, use_search=use_search, tools=tools)


def needs_web_search(prompt: str) -> bool:
    p = prompt.lower()
    return any(k in p for k in _WEB_KEYWORDS)

# ── 模型選擇 ──────────────────────

def _select_model(prompt: str) -> tuple[str, bool]:
    """回傳 (model, use_search)。"""
    p          = prompt.lower()
    use_search = needs_web_search(prompt)

    # ── 1. 使用者明確指定 ──────────────────────
    for keyword, model in _MODEL_OVERRIDES:
        if keyword in p:
            if use_search and not is_gemini(model):
                logger.warning(
                    "[agent_router] user override %s 不支援搜尋，升級為 %s",
                    model, GROUNDING_MIN_MODEL,
                )
                return GROUNDING_MIN_MODEL, use_search
            logger.info("[agent_router] user_override=%s", model)
            return model, use_search

    # ── 2. 需要搜尋 → 強制 Gemini ──────────────────────
    if use_search:
        model = DEFAULT_MODEL if is_gemini(DEFAULT_MODEL) else GROUNDING_MIN_MODEL
        return model, True

    # ── 3. 程式 / 數學 / 分析 → Flash ──────────────────────
    if any(kw in p for kw in _PRO_KEYWORDS):
        return MODELS["flash"], False

    # ── 4. 預設 ──────────────────────
    return DEFAULT_MODEL, False

# ── Tool 執行 ──────────────────────

async def execute_tools(
    decision: RouteDecision,
    user_id:  str,
    query:    str,
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
            tasks.append(executor(user_id, query))
        else:
            logger.warning("[agent_router] 未知工具名稱: %s", name)

    if not tasks:
        return []

    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in gathered if isinstance(r, str) and r]
