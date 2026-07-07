"""
core/link_preview/twitter.py

Modification():

- 新增本檔案：補上 Twitter/X 平台支援。Discord 原生對
  twitter.com / x.com 連結的 embed 支援長期不佳（經常顯示空白
  或極簡內容，影片完全無法內嵌），是社群公認需要代理服務修正
  的平台，因此比照 Instagram、Threads 的做法處理。

職責：

- 將 Twitter/X 貼文連結轉換為 LinkPreview
- 改用社群維運的公開代理服務取得完整的 og:title /
  og:description / og:image，多數代理服務對含影片的貼文亦提供
  og:video 可直接下載
- 透過 core.link_preview.fallback 依序嘗試多個候選網域，任一個
  能連上即可，候選清單由 settings.json 的
  link_preview.twitter_proxy_hosts 控制
"""

from __future__ import annotations

import logging
import re

from core.link_preview.base import LinkPreview
from core.link_preview.fallback import try_hosts
from core.link_preview.og_meta import extract_og_tags
from core.system.settings import get_list

logger = logging.getLogger("bot.link_preview.twitter")

_TWITTER_HOST_RE = re.compile(r"(www\.)?(twitter\.com|x\.com)", re.IGNORECASE)

# 候選代理網域的內建後備清單；settings.json 未設定時使用此值
_DEFAULT_HOSTS = ["fxtwitter.com", "vxtwitter.com"]


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 Twitter/X 連結轉換為 LinkPreview，失敗時回傳 None。"""
    hosts = get_list("link_preview.twitter_proxy_hosts", _DEFAULT_HOSTS)

    response = await try_hosts(
        build_url      = lambda host: _TWITTER_HOST_RE.sub(host, url, count=1),
        hosts          = hosts,
        platform_label = "Twitter",
    )
    if response is None:
        return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[Twitter] 未取得任何 og 標籤 url=%s", url)
        return None

    return LinkPreview(
        platform       = "twitter",
        platform_label = "Twitter / X",
        source_label   = f"via {response.url.host}",
        url            = url,
        title          = tags.get("title"),
        description    = tags.get("description"),
        thumbnail_url  = tags.get("image"),
        video_url      = tags.get("video"),
        color          = 0x1D9BF0,
    )
