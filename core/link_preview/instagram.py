"""
core/link_preview/instagram.py

職責：
- 將 Instagram 貼文／Reels 連結轉換為 LinkPreview。
- Instagram 的 og:* meta 標籤在非登入狀態下通常為空或僅含通用說明，
  因此改用 ddinstagram.com（社群維護的公開代理服務），它能在
  伺服器端模擬登入後回傳完整的 og:title / og:description / og:image。

Modification():

- 新增本檔案：補充 Instagram 平台支援，供 registry.py 在偵測到
  instagram.com 連結時呼叫。
- 使用 ddinstagram.com 代理：將 www.instagram.com 替換為
  ddinstagram.com，此服務回傳含完整 og:* 的靜態 HTML。
  代理服務由社群維護，若該服務停止運作，只需在此修改 _PROXY_HOST
  常數即可切換至其他代理（例如 kkinstagram.com），不需修改 Cog 層。
- 不附加影片：Instagram Reels 的影片 URL 來自 CDN 並有時效性，
  代理頁面 og:video 欄位不穩定；若有需求可後續啟用並實作下載。

備註：
- 若 ddinstagram.com 回傳空的 og:* 標籤（例如私人帳號或特定地區
  封鎖），extract() 回傳 None，Cog 層靜默略過此連結。
"""

from __future__ import annotations

import logging
import re

from core.link_preview.base import LinkPreview
from core.link_preview.http import build_client
from core.link_preview.og_meta import extract_og_tags

logger = logging.getLogger("bot.link_preview.instagram")

# ── 代理域名 ──────────────────────
_PROXY_HOST    = "ddinstagram.com"
_INSTA_HOST_RE = re.compile(r"(www\.)?instagram\.com", re.IGNORECASE)


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 Instagram 連結轉換為 LinkPreview，失敗時回傳 None。"""
    proxy_url = _INSTA_HOST_RE.sub(_PROXY_HOST, url, count=1)

    async with build_client() as client:
        try:
            response = await client.get(proxy_url)
            response.raise_for_status()
        except Exception:
            logger.exception("[Instagram] 代理請求失敗 url=%s proxy=%s", url, proxy_url)
            return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[Instagram] 未取得任何 og 標籤 url=%s", url)
        return None

    return LinkPreview(
        platform       = "instagram",
        platform_label = "Instagram",
        source_label   = f"via {_PROXY_HOST}",
        url            = url,
        title          = tags.get("title"),
        description    = tags.get("description"),
        thumbnail_url  = tags.get("image"),
        color          = 0xE1306C,
    )
