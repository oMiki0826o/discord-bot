"""
core/link_preview/pinterest.py

職責：
- 解析 Pinterest Pin 連結，透過 og:* meta 標籤取得標題、說明文字
  與圖片網址，組裝成 LinkPreview。

Modification():

- 新增本檔案：擴充連結預覽支援 Pinterest 圖片擷取（含 pin.it 短連結）。
- pin.it 為短連結，http.build_client() 已設定 follow_redirects=True，
  請求會自動導向至 pinterest.com/pin/... 頁面並回傳該頁 HTML，
  不需要像 Bilibili 的 b23.tv 那樣額外解析重定向網址。
- 使用 response.url（即重定向後的最終 URL）作為 LinkPreview.url，
  確保傳遞給 Cog 層的網址是完整的 pinterest.com/pin/... 格式，
  而非原始的 pin.it 短連結。
"""

from __future__ import annotations

import logging

from core.link_preview.base import LinkPreview
from core.link_preview.http import build_client
from core.link_preview.og_meta import extract_og_tags

logger = logging.getLogger("bot.link_preview.pinterest")


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 Pinterest Pin 連結（含 pin.it 短連結）轉換為 LinkPreview。"""
    async with build_client() as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except Exception:
            logger.exception("[Pinterest] 頁面請求失敗 url=%s", url)
            return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[Pinterest] 未取得任何 og 標籤 url=%s", url)
        return None

    return LinkPreview(
        platform       = "pinterest",
        platform_label = "Pinterest",
        source_label   = "Pinterest",
        url            = str(response.url),
        title          = tags.get("title"),
        description    = tags.get("description"),
        thumbnail_url  = tags.get("image"),
        color          = 0xE60023,
    )
