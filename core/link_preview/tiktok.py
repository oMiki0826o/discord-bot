"""
core/link_preview/tiktok.py

Modification():

- 新增「候選網域全部失敗時，最後嘗試原始網址本身」的備援，與
  instagram.py／threads.py／twitter.py 統一做法：把解析出的原始
  hostname 併入候選清單最後一位。TikTok 短連結（vt.tiktok.com /
  vm.tiktok.com）本身就是需要重定向的短網址，core.link_preview.http
  的 build_client() 已開啟 follow_redirects=True，直接請求短連結
  一樣會被正確導向最終內容頁面，這一步並非完全沒有意義。
- 新增本檔案：補上 TikTok 平台支援。Discord 原生對 tiktok.com
  連結的 embed 支援不穩定，短連結（vt.tiktok.com、vm.tiktok.com）
  經常完全無法預覽，比照 Instagram、Threads、Twitter 的做法處理。

職責：

- 將 TikTok 影片連結轉換為 LinkPreview
- 改用社群維運的公開代理服務取得完整的 og:title /
  og:description / og:image / og:video
- 透過 core.link_preview.fallback 依序嘗試多個候選網域，候選清單
  由 settings.json 的 link_preview.tiktok_proxy_hosts 控制，全部
  失敗時退回原始網址
- TikTok 短連結（vt.tiktok.com / vm.tiktok.com）與一般連結
  （www.tiktok.com）網域結構不同，網域替換規則需同時涵蓋兩者
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

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
    hosts = list(get_list("link_preview.tiktok_proxy_hosts", _DEFAULT_HOSTS))

    # 所有代理都失敗時，最後試一次原始網址本身（見上方 Modification 說明）。
    original_host = urlsplit(url).hostname
    if original_host and original_host not in hosts:
        hosts.append(original_host)

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
