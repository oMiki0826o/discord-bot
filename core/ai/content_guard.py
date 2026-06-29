"""
core/ai/content_guard.py

Modification():
- 統一檔案註解格式，保留原有職責說明。

職責：
- 讀取 Owner 自訂的內容審核規則檔（database/ai/moderation_rules.txt）
- 提供 moderation_to_prompt()，將規則以自然語言形式注入 system prompt

設計理念：
- 與 utils.ai.prompt_guard 是兩條平行機制，職責不同：
    - prompt_guard：處理「使用者想操控 AI」（提示詞注入偵測）
    - content_guard（本檔）：處理「Owner 想限制 AI 的回應範圍」
      （NSFW、仇恨言論、特定話題等內容審核）
- 採用獨立 .txt 檔讓 Owner 用自然語言自由編寫規則，而非寫死的
  關鍵字黑名單：
    1. Owner 不需要懂程式就能調整規範
    2. 自然語言規則比關鍵字比對更有彈性（例如可以寫「避免討論
       特定政治立場的爭議」而不需要列舉所有相關詞彙）
    3. 與 database/ai/background.txt（角色背景設定）採用相同模式，
       對熟悉本專案的 Owner 來說維護方式一致
- 檔案不存在或為空時，moderation_to_prompt() 回傳空字串，
  不影響任何現有行為（純粹是選用功能）
- 支援 "#" 開頭的註解行（讀取時自動過濾），讓 Owner 可在檔案中
  保留使用說明而不會被當成審核規則本身送進 prompt

快取策略：
- 內容快取在記憶體，並記錄檔案 mtime；每次呼叫時用一次
  os.stat()（成本極低）檢查檔案是否變動，變動才重新讀取，
  Owner 編輯檔案後下次請求即可生效，不需重啟 Bot
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("bot.content_guard")

# core/ai/content_guard.py → core/ai → core → 專案根目錄
_RULES_FILE = (
    Path(__file__).resolve().parents[2] / "database" / "ai" / "moderation_rules.txt"
)

# ── 快取狀態 ──────────────────────

_cached_content:  str = ""
_cached_mtime:    float = -1.0

# ── 內部工具 ──────────────────────

def _reload_if_changed() -> str:
    """檢查檔案 mtime，變動時重新讀取，否則回傳快取內容。"""
    global _cached_content, _cached_mtime

    if not _RULES_FILE.exists():
        if _cached_mtime != -1.0:
            logger.info("[content_guard] moderation_rules.txt 已被移除，清除快取")
            _cached_content, _cached_mtime = "", -1.0
        return ""

    try:
        mtime = _RULES_FILE.stat().st_mtime
    except OSError:
        return _cached_content

    if mtime == _cached_mtime:
        return _cached_content

    try:
        text = _RULES_FILE.read_text(encoding="utf-8")
        # ── 過濾 "#" 開頭的註解行，讓 Owner 可在檔案中寫使用說明 ──────────────────────
        kept = [
            line for line in text.splitlines()
            if not line.strip().startswith("#")
        ]
        _cached_content = "\n".join(kept).strip()
        _cached_mtime    = mtime
        logger.info(
            "[content_guard] moderation_rules.txt 已重新載入（%d 字元）",
            len(_cached_content),
        )
    except Exception as e:
        logger.warning("[content_guard] 讀取 moderation_rules.txt 失敗: %s", e)

    return _cached_content

# ── 對外入口 ──────────────────────

def get_rules_text() -> str:
    """取得目前的審核規則原文（供 $dashboard 等指令顯示用）。"""
    return _reload_if_changed()


def moderation_to_prompt() -> str:
    """
    回傳可直接插入 prompt 的審核規則區塊。
    檔案不存在或為空時回傳空字串（不影響現有行為）。
    """
    content = _reload_if_changed()
    if not content:
        return ""
    return f"=== 內容規範（由管理者設定，優先遵守）===\n{content}"


def reload_rules() -> str:
    """強制清除快取並重新讀取，供 $dashboard rules reload 使用。"""
    global _cached_mtime
    _cached_mtime = -1.0
    return _reload_if_changed()
