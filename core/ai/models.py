"""
core/ai/models.py

Modification():
- 集中定義專案中所有會用到的 Gemini / Gemma 模型名稱常數。
- 提供 Gemini 判斷工具，供路由、呼叫層與多模態流程共用。
- 新增 MULTIMODAL_MODEL，讓圖片附件不再依賴散落的硬編碼模型名稱。

職責：
- 作為模型名稱與模型用途的唯一來源。
- 降低更換模型版本時漏改其他模組的風險。
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

DEFAULT_MODEL       = MODELS["gemma"]    # 一般對話預設模型
GROUNDING_MIN_MODEL = MODELS["flash"]    # 需要 Google Search Grounding 時的最低模型
MULTIMODAL_MODEL    = MODELS["flash"]    # 圖片 / 未來多模態附件的預設模型
FALLBACK_MODEL      = MODELS["lite"]     # 主模型失敗時的備援模型


# ── 工具函式 ──────────────────────

def is_gemini(model: str) -> bool:
    """判斷模型是否為 Gemini 系列（Gemma 不支援 system_instruction / Grounding）。"""
    return model.lower().startswith("gemini-")
