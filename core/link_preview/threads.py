"""
core/link_preview/threads.py

職責：
- 將 Threads 貼文連結轉換為 LinkPreview。
- Threads（threads.net / threads.com）的 Discord 原生 Embed 僅顯示
  純連結，沒有任何預覽內容；改用 fixthreads.net 代理服務，它能
  回傳含 og:title / og:description / og:image 的靜態 HTML。

Modification():

- 新增本檔案：補充 Threads 平台支援，供 registry.py 在偵測到
  threads.net / threads.com 連結時呼叫。
- 使用 fixthreads.net 代理：將 www.threads.net 或 www.threads.com
  替換為 www.fixthreads.net，此服務回傳含 og:* 的靜態 HTML。
  若代理服務停止運作，只需修改 _PROXY_HOST 常數即可切換，不需
  修改 Cog 層或 detector。

備註：
- threads.com 和 threads.net 是同一服務的不同 TLD，在偵測端
  （detector.py）皆已覆蓋；本擷取器只需替換域名部分，
  路徑結構相同，因此同一套正規表示式即可處理兩者。
"""

from __future__ import annotations

import logging
import re

from core.link_preview.base import LinkPreview
from core.link_preview.http import build_client
from core.link_preview.og_meta import extract_og_tags

logger = logging.getLogger("bot.link_preview.threads")

# ── 代理域名 ──────────────────────
_PROXY_HOST       = "fixthreads.net"
_THREADS_HOST_RE  = re.compile(r"(www\.)?threads\.(net|com)", re.IGNORECASE)


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 Threads 連結轉換為 LinkPreview，失敗時回傳 None。"""
    proxy_url = _THREADS_HOST_RE.sub(f"www.{_PROXY_HOST}", url, count=1)

    async with build_client() as client:
        try:
            response = await client.get(proxy_url)
            response.raise_for_status()
        except Exception:
            logger.exception("[Threads] 代理請求失敗 url=%s proxy=%s", url, proxy_url)
            return None

    tags = extract_og_tags(response.text)
    if not tags:
        logger.warning("[Threads] 未取得任何 og 標籤 url=%s", url)
        return None

    return LinkPreview(
        platform       = "threads",
        platform_label = "Threads",
        source_label   = f"via {_PROXY_HOST}",
        url            = url,
        title          = tags.get("title"),
        description    = tags.get("description"),
        thumbnail_url  = tags.get("image"),
        color          = 0x101010,
    )
