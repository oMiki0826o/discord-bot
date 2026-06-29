"""
core/ai/models.py

職責：
- 集中定義專案中所有會用到的 Gemini / Gemma 模型名稱常數
- 提供 is_gemini() 判斷工具，供路由與呼叫層共用

修正（新增此檔案，解決模型名稱字串重複定義問題）：
- 原本 "gemini-3.1-flash-lite" 等字串分別硬編碼於：
    core/ai/agent_router.py（MODELS、FALLBACK 判斷）
    core/ai/core.py（FALLBACK_MODEL）
    core/ai/memory_manager.py（_EXTRACT_MODEL、_SUMMARY_MODEL）
    core/ai/user_context.py（_PROFILE_MODEL）
  共 5 處，日後更換模型版本需逐一修改且容易遺漏
- 改為全部從此檔案匯入，更換模型版本時只需修改 MODELS 字典一處

模型對應（依使用情境分類）：
- lite  → gemini-3.1-flash-lite（輕量任務：記憶擷取、摘要、偏好分析、Fallback）
- flash → gemini-2.5-flash（需要搜尋 / 程式 / 數學等較複雜任務）
- gemma → gemma-4-31b-it（一般對話預設模型）
- embed → text-embedding-004（向量嵌入）
"""

from __future__ import annotations

# ── 對話 / 生成模型 ──────────────────────

MODELS: dict[str, str] = {
    "lite":  "gemini-3.1-flash-lite",
    "flash": "gemini-2.5-flash",
    "gemma": "gemma-4-31b-it",
}

# ── 嵌入模型 ──────────────────────

EMBED_MODEL: str = "text-embedding-004"

# ── 預設 / 特殊用途模型 ──────────────────────

DEFAULT_MODEL       = MODELS["gemma"]   # 一般對話預設模型
GROUNDING_MIN_MODEL = MODELS["flash"]   # 需要 Google Search Grounding 時的最低模型
FALLBACK_MODEL      = MODELS["lite"]    # 主模型失敗時的備援模型


# ── 工具函式 ──────────────────────

def is_gemini(model: str) -> bool:
    """判斷模型是否為 Gemini 系列（Gemma 不支援 system_instruction / Grounding）。"""
    return model.lower().startswith("gemini-")
