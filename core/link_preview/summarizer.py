"""
core/link_preview/summarizer.py

Modification():

- 新增本檔案：連結預覽與關鍵字摘要功能共用的 Gemma 摘要生成器
- 改用 core.ai.gemini_client 提供的全域共用 Client，取代原本自行以
  os.getenv("GEMINI_API") 建立獨立 genai.Client 的作法；專案已在
  core/ai/gemini_client.py 集中管理唯一的 Client 實例，目的就是
  避免多處各自初始化造成連線資源浪費，此檔案理應沿用同一原則
- 摘要輸入截斷長度改為讀取 link_preview.summary_input_max_chars，
  供關鍵字觸發的整頁文章摘要功能（core/link_preview/article.py）
  調整輸入長度時使用

職責：

- 將擷取到的長文字內容（貼文說明、影片簡介、網頁純文字等）透過
  Gemma 模型生成精簡摘要，用於 Embed 的說明欄位或摘要回覆
- 特意使用 MODELS["gemma"]（core/ai/models.py）而非 Gemini 系列，
  因為連結預覽只是單輪、無上下文的摘要任務，Gemma 成本較低，
  不需要動用主要對話模型的 token 額度

備註：

- 不依賴 core/ai 既有的對話／記憶／限速邏輯：連結預覽是一次性
  摘要任務，沒有對話上下文，混用既有對話管線只會徒增耦合。
  若未來要共用限速器，可在 summarize() 內呼叫既有的限速模組，
  呼叫端介面不需變動
"""

from __future__ import annotations

import logging

from core.ai.gemini_client import client
from core.ai.models import MODELS
from core.system.settings import get_int

logger = logging.getLogger("bot.link_preview.summarizer")

_PROMPT_TEMPLATE = (
    "請將以下內容濃縮為繁體中文摘要，{max_chars} 字以內，"
    "只保留重點，不要加入任何開場白或解釋：\n\n{content}"
)


# ── 對外介面 ──────────────────────

async def summarize(content: str) -> str | None:
    """
    使用 Gemma 生成內容摘要。

    任何失敗（上游錯誤、逾時）皆回傳 None，呼叫端應將其視為
    「摘要不可用」，改用原始 description 欄位，而不是讓連結預覽
    整體失敗。
    """
    content = content.strip()
    if not content:
        return None

    max_chars       = get_int("link_preview.summary_max_chars", 200)
    max_input_chars = get_int("link_preview.summary_input_max_chars", 4000)
    # 截斷輸入長度：控制 token 用量，且過長的原文對摘要品質幫助有限
    prompt = _PROMPT_TEMPLATE.format(max_chars=max_chars, content=content[:max_input_chars])

    try:
        response = await client.aio.models.generate_content(
            model    = MODELS["gemma"],
            contents = prompt,
        )
        summary = (response.text or "").strip()
        return summary or None
    except Exception:
        logger.exception("[摘要] Gemma 生成摘要失敗")
        return None
