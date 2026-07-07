"""
core/link_preview/instagram.py

Modification():

- 修正 ddinstagram.com 網域完全無法解析（DNS gaierror）導致
  Instagram 預覽整個失效的問題：改用 core.link_preview.fallback
  依序嘗試多個候選代理網域，任一個能連上即可，不再綁死單一網域。
  候選清單由 settings.json 的 link_preview.instagram_proxy_hosts
  控制，之後若某個代理服務又停止運作，只需調整設定即可，
  不需要修改程式碼。
- 新增讀取 og:video 標籤：部分代理服務會在頁面中提供可直接下載
  的影片網址，取得後交由 Cog 層決定是否下載並以附件形式內嵌播放。

職責：

- 將 Instagram 貼文／Reels 連結轉換為 LinkPreview
- Instagram 的 og:* meta 標籤在未登入狀態下通常為空或僅含通用
  說明，因此改用社群維運的公開代理服務，於伺服器端模擬登入後
  回傳完整的 og:title / og:description / og:image（部分服務
  亦提供 og:video）
"""

from __future__ import annotations

import logging
import re

from core.link_preview.base import LinkPreview
from core.link_preview.fallback import try_hosts
from core.link_preview.og_meta import extract_og_tags
from core.system.settings import get_list

logger = logging.getLogger("bot.link_preview.instagram")

_INSTA_HOST_RE = re.compile(r"(www\.)?instagram\.com", re.IGNORECASE)

# 候選代理網域的內建後備清單；settings.json 未設定時使用此值
_DEFAULT_HOSTS = ["ddinstagram.com", "kkinstagram.com", "d.ddinstagram.com"]


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 Instagram 連結轉換為 LinkPreview，失敗時回傳 None。"""
    hosts = get_list("link_preview.instagram_proxy_hosts", _DEFAULT_HOSTS)

    response = await try_hosts(
        build_url      = lambda host: _INSTA_HOST_RE.sub(host, url, count=1),
        hosts          = hosts,
        platform_label = "Instagram",
    )
    if response is None:
        return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[Instagram] 未取得任何 og 標籤 url=%s", url)
        return None

    return LinkPreview(
        platform       = "instagram",
        platform_label = "Instagram",
        source_label   = f"via {response.url.host}",
        url            = url,
        title          = tags.get("title"),
        description    = tags.get("description"),
        thumbnail_url  = tags.get("image"),
        video_url      = tags.get("video"),
        color          = 0xE1306C,
    )
