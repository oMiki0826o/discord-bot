"""
core/link_preview/tiktok.py

Modification():

- 新增本檔案：補上 TikTok 平台支援。Discord 原生對 tiktok.com
  連結的 embed 支援不穩定，短連結（vt.tiktok.com、vm.tiktok.com）
  經常完全無法預覽，比照 Instagram、Threads、Twitter 的做法處理。

職責：

- 將 TikTok 影片連結轉換為 LinkPreview
- 改用社群維運的公開代理服務取得完整的 og:title /
  og:description / og:image / og:video
- 透過 core.link_preview.fallback 依序嘗試多個候選網域，候選清單
  由 settings.json 的 link_preview.tiktok_proxy_hosts 控制
- TikTok 短連結（vt.tiktok.com / vm.tiktok.com）與一般連結
  （www.tiktok.com）網域結構不同，網域替換規則需同時涵蓋兩者
"""

from __future__ import annotations

import logging
import re

from core.link_preview.base import LinkPreview
from core.link_preview.fallback import try_hosts
from core.link_preview.og_meta import extract_og_tags
from core.system.settings import get_list

logger = logging.getLogger("bot.link_preview.tiktok")

# 涵蓋一般連結（www.tiktok.com/@user/video/123）與短連結
# （vt.tiktok.com/xxx、vm.tiktok.com/xxx）
_TIKTOK_HOST_RE = re.compile(
    r"(www\.|vt\.|vm\.)?tiktok\.com", re.IGNORECASE
)

# 候選代理網域的內建後備清單；settings.json 未設定時使用此值
_DEFAULT_HOSTS = ["tnktok.com", "vxtiktok.com"]


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 TikTok 連結轉換為 LinkPreview，失敗時回傳 None。"""
    hosts = get_list("link_preview.tiktok_proxy_hosts", _DEFAULT_HOSTS)

    response = await try_hosts(
        build_url      = lambda host: _TIKTOK_HOST_RE.sub(host, url, count=1),
        hosts          = hosts,
        platform_label = "TikTok",
    )
    if response is None:
        return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[TikTok] 未取得任何 og 標籤 url=%s", url)
        return None

    return LinkPreview(
        platform       = "tiktok",
        platform_label = "TikTok",
        source_label   = f"via {response.url.host}",
        url            = url,
        title          = tags.get("title"),
        description    = tags.get("description"),
        thumbnail_url  = tags.get("image"),
        video_url      = tags.get("video"),
        color          = 0x000000,
    )
