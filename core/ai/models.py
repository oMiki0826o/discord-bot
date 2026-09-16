"""
core/ai/models.py

Modification():
- 修正 EMBED_MODEL：原本的 "text-embedding-004" 已於 2026/1/14 正式
  棄用，實際呼叫時會收到 404（models/text-embedding-004 is not found
  for API version v1beta），導致 memory_manager 的向量化與語意搜尋
  一直靜默失敗。改為官方後繼模型 "gemini-embedding-001"，呼叫方式
  相容（僅需替換模型名稱，embed_content() 介面不變）。
- 因為全專案僅有本檔案定義 EMBED_MODEL 這一個字串常數，
  這次替換只需要改這一處，其餘模組（memory_manager.py）皆透過
  import 取得，不需逐一修改，這正是集中管理模型名稱的目的。

職責：
- 作為模型名稱與模型用途的唯一來源。
- 降低更換模型版本時漏改其他模組的風險。
"""

from __future__ import annotations

import logging

from core.system.settings import get_list, get_str

logger = logging.getLogger("bot.ai.models")

# ── 對話 / 生成模型 ──────────────────────

MODELS: dict[str, str] = {
    "lite":  "gemini-3.1-flash-lite",
    "flash": "gemini-2.5-flash",
    "gemma": "gemma-4-31b-it",
}

MODEL_CATEGORIES: tuple[str, ...] = ("gemini", "flash", "gemma")

# 使用者只選擇三大類；類別內模型依序輪替。
# 這份內建清單同時是 settings.json 缺值或格式錯誤時的安全回退。
DEFAULT_MODEL_POOLS: dict[str, tuple[str, ...]] = {
    "gemini": (
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    ),
    "flash": (
        "gemini-2.5-flash",
        "gemini-3-flash-preview",
        "gemini-3.5-flash",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.8-flash",
    ),
    "gemma": (
        "gemma-4-31b-it",
    ),
}

# ── 嵌入模型 ──────────────────────
#
# gemini-embedding-001：目前官方建議的穩定文字嵌入模型，向下相容
# embed_content() 呼叫介面，預設輸出 3072 維向量（可用
# EmbedContentConfig(output_dimensionality=...) 縮減，例如 768 / 1536）。
#
# 若資料庫中留有舊模型（text-embedding-004，768 維）產生的向量：
# 維度不同時 cosine 相似度比對會直接視為不相似（回傳 0），不會噴錯，
# 但也不會比對到；純語意搜尋以外的關鍵字比對不受影響。若要讓舊資料
# 也能被語意搜尋比對到，需要用新模型重新產生一次向量。
#
# 另有較新的 gemini-embedding-2，但其 task_type 參數處理方式與本模型
# 不同（部分版本會靜默忽略 task_type），日後若考慮升級請先確認
# task_type 行為是否符合預期，避免語意搜尋品質無聲下降。

EMBED_MODEL: str = "gemini-embedding-001"

# ── 預設 / 特殊用途模型 ──────────────────────

DEFAULT_CATEGORY    = "gemini"
GROUNDING_CATEGORY  = "flash"
MULTIMODAL_CATEGORY = "flash"


def get_default_category() -> str:
    """從 settings.json 取得預設類別，並兼容舊的 lite 設定。"""
    value = get_str("ai.default_model", DEFAULT_CATEGORY).strip().lower()
    if value == "lite":
        value = "gemini"
    if value not in MODEL_CATEGORIES:
        logger.warning(
            "[models] ai.default_model=%r 無效，使用 %s", value, DEFAULT_CATEGORY,
        )
        return DEFAULT_CATEGORY
    return value


def get_model_pool(category: str) -> tuple[str, ...]:
    """取得模型類別的熱重載輪替清單，自動去除空值與重複值。"""
    normalized = category.strip().lower()
    fallback = DEFAULT_MODEL_POOLS.get(normalized)
    if fallback is None:
        normalized = DEFAULT_CATEGORY
        fallback = DEFAULT_MODEL_POOLS[normalized]

    configured = get_list(f"ai.model_pools.{normalized}", list(fallback))
    models = tuple(dict.fromkeys(
        item.strip() for item in configured
        if isinstance(item, str) and item.strip()
    ))
    if models:
        return models

    logger.warning("[models] %s 模型池為空，使用內建清單", normalized)
    return fallback


def get_primary_model(category: str) -> str:
    """回傳類別中第一個（優先）模型。"""
    return get_model_pool(category)[0]


def get_model_candidates(category: str, preferred: str | None = None) -> tuple[str, ...]:
    """回傳一次請求的候選順序，指定模型可優先排在最前。"""
    pool = get_model_pool(category)
    if not preferred or preferred not in pool:
        return pool
    return (preferred, *(model for model in pool if model != preferred))


def category_for_model(model: str) -> str:
    """由模型 ID 反查類別，供相容舊呼叫與測試使用。"""
    for category in MODEL_CATEGORIES:
        if model in get_model_pool(category):
            return category
    return "gemini" if is_gemini(model) else "gemma"


# ── 工具函式 ──────────────────────

def is_gemini(model: str) -> bool:
    """判斷模型是否為 Gemini 系列（Gemma 不支援 system_instruction / Grounding）。"""
    return model.lower().startswith("gemini-")
