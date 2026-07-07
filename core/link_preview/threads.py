"""
core/link_preview/threads.py

Modification():

- 修正 fixthreads.net 暫時回應 502 Bad Gateway 導致 Threads 預覽
  失效的問題：改用 core.link_preview.fallback 依序嘗試多個候選
  代理網域，單一服務的暫時性故障不再讓整個平台預覽失敗。
  候選清單由 settings.json 的 link_preview.threads_proxy_hosts
  控制。
- 新增讀取 og:video 標籤，供 Cog 層決定是否下載內嵌播放。

職責：

- 將 Threads 貼文連結轉換為 LinkPreview
- Threads（threads.net / threads.com）的 Discord 原生 Embed 僅
  顯示純連結，沒有任何預覽內容；改用社群維運的代理服務取得完整
  的 og:title / og:description / og:image（部分服務亦提供
  og:video）
"""

from __future__ import annotations

import logging
import re

from core.link_preview.base import LinkPreview
from core.link_preview.fallback import try_hosts
from core.link_preview.og_meta import extract_og_tags
from core.system.settings import get_list

logger = logging.getLogger("bot.link_preview.threads")

_THREADS_HOST_RE = re.compile(r"(www\.)?threads\.(net|com)", re.IGNORECASE)

# 候選代理網域的內建後備清單；settings.json 未設定時使用此值
_DEFAULT_HOSTS = ["www.fixthreads.net", "www.vxthreads.net"]


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 Threads 連結轉換為 LinkPreview，失敗時回傳 None。"""
    hosts = get_list("link_preview.threads_proxy_hosts", _DEFAULT_HOSTS)

    response = await try_hosts(
        build_url      = lambda host: _THREADS_HOST_RE.sub(host, url, count=1),
        hosts          = hosts,
        platform_label = "Threads",
    )
    if response is None:
        return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[Threads] 未取得任何 og 標籤 url=%s", url)
        return None

    return LinkPreview(
        platform       = "threads",
        platform_label = "Threads",
        source_label   = f"via {response.url.host}",
        url            = url,
        title          = tags.get("title"),
        description    = tags.get("description"),
        thumbnail_url  = tags.get("image"),
        video_url      = tags.get("video"),
        color          = 0x101010,
    )
