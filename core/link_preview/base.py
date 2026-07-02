"""
core/link_preview/base.py

職責：
- 定義 LinkPreview 與 LinkStat 兩個跨平台共用的資料容器。
- 所有擷取器（bilibili / instagram / threads / pinterest）皆回傳
  LinkPreview，Cog 層透過統一介面組裝 Embed，不需分辨平台差異。

Modification():

- 新增本檔案：作為 core.link_preview 的資料層基礎，讓所有平台
  擷取器共用相同的輸出型別，新增平台時不需修改 Cog 層的組裝邏輯。
- summary 欄位設計為可選且可後設置（Optional, mutable）：
  LinkPreview 由擷取器建立時不包含摘要，由 Cog 的 _maybe_summarize()
  呼叫 Gemma 後才寫入，避免將摘要生成邏輯耦合進各平台擷取器。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── 統計數據 ──────────────────────

@dataclass(slots=True)
class LinkStat:
    """
    單筆統計數據，顯示在 Embed 的統計欄位列。
    icon 與 value 由各平台擷取器自行決定格式，
    Cog 只負責 join 後放進 description，不解析語義。
    """

    icon:  str  # 代表此數據類型的圖示（建議使用 Unicode 符號）
    value: str  # 格式化後的數值字串，例如 "10.5萬"


# ── 連結預覽 ──────────────────────

@dataclass
class LinkPreview:
    """
    單一連結的完整預覽資料。

    由各平台擷取器（bilibili / instagram / pinterest / threads）建立，
    傳遞給 Cog 層後由 _build_embed() 組裝成 discord.Embed。

    summary 欄位在擷取後由 Cog 的 _maybe_summarize() 寫入，
    建立時維持 None；設計上刻意不使用 frozen=True 以允許此後設置。
    """

    # ── 必填欄位 ──────────────────────
    platform:       str            # 內部識別字串，例如 "bilibili"
    platform_label: str            # 顯示名稱，例如 "BiliBili"
    source_label:   str            # 來源說明，例如 "BiliFix / vxbilibili.com"
    url:            str            # 最終頁面網址（重定向後）

    # ── 選填內容欄位 ──────────────────────
    title:          str | None     = None
    author:         str | None     = None
    description:    str | None     = None
    thumbnail_url:  str | None     = None
    video_url:      str | None     = None
    stats:          list[LinkStat] = field(default_factory=list)
    color:          int            = 0x5865F2  # Discord blurple 預設色

    # ── 後設欄位（由 Cog 層在回覆前寫入） ──────────────────────
    summary: str | None = None
