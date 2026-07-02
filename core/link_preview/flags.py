"""
core/link_preview/flags.py

職責：
- 提供 get_flag()，讀取 settings.json 中的布林設定值。
- 集中管理連結預覽功能的所有功能開關，避免直接呼叫散落在各擷取器
  中的 settings.get_bool()，讓未來替換設定來源（例如改為資料庫
  每伺服器設定）時只需修改此處，不需改動各平台擷取器。

Modification():

- 新增本檔案：為 core.link_preview 提供集中的布林設定讀取介面。

目前使用的設定鍵：
    link_preview.enabled               全局開關（預設 True）
    link_preview.attach_video          是否附加影片檔案（預設 True）
    link_preview.bilibili_fetch_video  是否為 Bilibili 額外取得播放 URL（預設 False）
"""

from __future__ import annotations

from core.system.settings import get_bool


# ── 對外介面 ──────────────────────

def get_flag(key: str, default: bool = False) -> bool:
    """
    從 settings.json 讀取布林設定值。

    設計為 get_bool 的薄包裝，統一命名語意（flag = 功能開關），
    讓呼叫端程式碼讀起來更明確：get_flag("link_preview.enabled")。
    """
    return get_bool(key, default)
