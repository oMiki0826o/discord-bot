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

# ── 對話 / 生成模型 ──────────────────────

MODELS: dict[str, str] = {
    "lite":  "gemini-3.1-flash-lite",
    "flash": "gemini-2.5-flash",
    "gemma": "gemma-4-31b-it",
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

DEFAULT_MODEL       = MODELS["gemma"]    # 一般對話預設模型
GROUNDING_MIN_MODEL = MODELS["flash"]    # 需要 Google Search Grounding 時的最低模型
MULTIMODAL_MODEL    = MODELS["flash"]    # 圖片 / 未來多模態附件的預設模型
FALLBACK_MODEL      = MODELS["lite"]     # 主模型失敗時的備援模型


# ── 工具函式 ──────────────────────

def is_gemini(model: str) -> bool:
    """判斷模型是否為 Gemini 系列（Gemma 不支援 system_instruction / Grounding）。"""
    return model.lower().startswith("gemini-")
