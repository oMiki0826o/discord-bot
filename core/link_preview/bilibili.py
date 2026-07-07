"""
core/link_preview/bilibili.py

Modification():

- 併入使用者提供的 cogs/events/bilibili.py 的關鍵修正：Bilibili API
  在缺少 Referer / Origin 標頭時有機率回傳 412，因此改為每次請求都
  帶上固定的 _BILI_HEADERS，而不是散落在多處各自組 headers
- 新增時長（duration）解析，比照統計數字共用同一套 LinkStat 顯示邏輯
- 不採用同步 requests：Discord 事件迴圈是非同步的，同步請求會整個
  卡住 Bot，維持既有的 httpx 非同步實作
- _format_duration 已整合至 utils.formatter.format_duration，移除
  本地重複實作
- link_preview.bilibili_fetch_video 預設值改為 True：原本預設關閉，
  只會顯示縮圖，實際上讓「內嵌播放影片」這個功能形同虛設。開啟後
  仍保留原本的降級路徑——影片超過大小上限、下載失敗、API 無法取得
  播放網址時，一律優雅退回「只顯示縮圖 + 連結」，不影響原有的
  文字資訊呈現
- _resolve_redirect 的短網址判斷改用 hostname 邊界比對（與
  detector.py 的修正方式一致），不再用粗糙的子字串搜尋

職責：

- 解析 b23.tv 短連結（跟隨重定向取得真正的 bilibili.com 網址）
- 呼叫 Bilibili 公開 API（x/web-interface/view）取得影片標題、簡介、
  封面、時長、UP 主與統計數據，組裝成統一的 LinkPreview 結構
- 視設定決定是否額外呼叫播放網址 API，取得可下載並內嵌播放的
  影片網址

備註：

- 使用的是 Bilibili 未強制登入即可存取的公開 API，僅需 bvid，
  不需要 Cookie。若未來 API 行為改變，可在 http.build_client() 統一
  附加 Cookie，不需更動本檔案邏輯
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from core.link_preview.base import LinkPreview, LinkStat
from core.link_preview.flags import get_flag
from core.link_preview.http import build_client
from core.link_preview.video import resolve_bilibili_play_url
from utils.formatter import format_duration

logger = logging.getLogger("bot.link_preview.bilibili")

_BVID_RE  = re.compile(r"BV[0-9A-Za-z]{10}")
_VIEW_API = "https://api.bilibili.com/x/web-interface/view"

# ── 防 412 headers ──────────────────────
# Bilibili API 對缺少 Referer / Origin 的請求有機率回應 412，
# 固定帶上官網網域即可穩定通過。
_BILI_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin":  "https://www.bilibili.com",
}


# ── 對外介面 ──────────────────────

async def extract(url: str) -> LinkPreview | None:
    """將 b23.tv / bilibili.com 影片連結轉換為 LinkPreview。"""
    async with build_client() as client:
        real_url = await _resolve_redirect(client, url)

        bvid_match = _BVID_RE.search(real_url)
        if bvid_match is None:
            logger.warning("[Bilibili] 無法從網址取得 bvid url=%s", real_url)
            return None
        bvid = bvid_match.group(0)

        try:
            response = await client.get(
                _VIEW_API, params={"bvid": bvid}, headers=_BILI_HEADERS
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("[Bilibili] API 請求失敗 url=%s", real_url)
            return None

    if payload.get("code") != 0:
        logger.warning(
            "[Bilibili] API 回傳錯誤 url=%s code=%s msg=%s",
            real_url, payload.get("code"), payload.get("message"),
        )
        return None

    data = payload.get("data", {})
    stat = data.get("stat", {})

    video_url = None
    cid       = data.get("cid")
    if cid and get_flag("link_preview.bilibili_fetch_video", True):
        video_url = await resolve_bilibili_play_url(bvid, cid)

    # ── 統計欄位 ──────────────────────
    stats = [
        LinkStat("觀看", _format_count(stat.get("view"))),
        LinkStat("點讚", _format_count(stat.get("like"))),
        LinkStat("投幣", _format_count(stat.get("coin"))),
        LinkStat("收藏", _format_count(stat.get("favorite"))),
        LinkStat("分享", _format_count(stat.get("share"))),
    ]
    duration = data.get("duration")
    if duration:
        stats.append(LinkStat("時長", format_duration(duration)))

    return LinkPreview(
        platform       = "bilibili",
        platform_label = "BiliBili",
        source_label   = "BiliBili",
        url            = real_url,
        title          = data.get("title"),
        author         = (data.get("owner") or {}).get("name"),
        description    = data.get("desc"),
        thumbnail_url  = data.get("pic"),
        video_url      = video_url,
        stats          = stats,
        color          = 0x00A1D6,
    )


# ── 內部工具 ──────────────────────

async def _resolve_redirect(client, url: str) -> str:
    """b23.tv 為短網址，需先發送請求取得最終導向的 bilibili.com 網址。"""
    hostname = (urlsplit(url).hostname or "").lower()
    if hostname != "b23.tv" and not hostname.endswith(".b23.tv"):
        return url
    try:
        response = await client.get(url, headers=_BILI_HEADERS)
        return str(response.url)
    except Exception:
        logger.exception("[Bilibili] 短網址重定向解析失敗 url=%s", url)
        return url


def _format_count(value: int | None) -> str:
    """將數字轉換為「萬」單位顯示，未取得資料時顯示 '-'。"""
    if value is None:
        return "-"
    if value >= 10_000:
        text = f"{value / 10_000:.1f}萬"
        return text.replace(".0萬", "萬")
    return str(value)
