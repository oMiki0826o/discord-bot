
"""
core/link_preview/bilibili.py

Bilibili link fixer.

功能：
- 支援 bilibili.com 影片連結
- 支援 b23.tv 短網址
- b23.tv 僅進行 HTTP redirect
- 從網址取得 BVID
- 產生乾淨的 vxbilibili.com 修復連結
- 完全不呼叫 Bilibili API
- 不抓影片標題
- 不抓影片資訊
- 不抓縮圖
- 不抓統計
- 不抓時長
- 不下載影片
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from core.link_preview.base import LinkPreview
from core.link_preview.http import build_client


logger = logging.getLogger("bot.link_preview.bilibili")


# Bilibili BV 編號
_BVID_RE = re.compile(r"BV[0-9A-Za-z]{10}")


async def extract(url: str) -> LinkPreview | None:
    """
    解析 Bilibili / B23 連結。

    不使用 Bilibili API。

    例如：

        https://www.bilibili.com/video/BV1Jr756pEhb

    會直接產生：

        https://www.vxbilibili.com/video/BV1Jr756pEhb/
    """

    # b23.tv 需要先取得 redirect 後的真正 URL。
    real_url = url

    hostname = (urlsplit(url).hostname or "").lower()

    if hostname == "b23.tv" or hostname.endswith(".b23.tv"):
        async with build_client() as client:
            real_url = await _resolve_redirect(
                client,
                url,
            )

    # 從 URL 中直接找 BVID。
    bvid_match = _BVID_RE.search(real_url)

    if bvid_match is None:
        logger.warning(
            "[Bilibili] 無法取得 BVID url=%s",
            real_url,
        )
        return None

    bvid = bvid_match.group(0)

    # 不使用 API。
    # 只根據 BVID 建立 vxbilibili 修復網址。
    fixed_link = _build_fixed_video_link(bvid)

    return LinkPreview(
        platform="bilibili",
        platform_label="BiliBili",
        source_label="BiliBili",
        url=real_url,

        # 不呼叫 API，因此沒有以下資訊。
        title=None,
        author=None,
        description=None,
        thumbnail_url=None,

        # Cog 會拿這個欄位發送 Discord 訊息。
        embed_video_link=fixed_link,

        stats=[],
        color=0x00A1D6,
    )


async def _resolve_redirect(
    client,
    url: str,
) -> str:
    """
    解析 b23.tv 短網址。

    注意：
    - 這不是 Bilibili API
    - 只是在取得短網址的 HTTP redirect
    """

    try:
        response = await client.get(url)

        return str(response.url)

    except Exception:
        logger.exception(
            "[Bilibili] b23.tv redirect 解析失敗 url=%s",
            url,
        )
        return url


def _build_fixed_video_link(bvid: str) -> str:
    """
    建立乾淨的 vxbilibili 連結。

    不保留任何原始 query parameters。
    """

    return f"https://www.vxbilibili.com/video/{bvid}/"

