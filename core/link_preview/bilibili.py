"""
core/link_preview/bilibili.py

Modification():

- 修正 embed_video_link 網址雜訊問題（實測截圖回報：Discord 完全沒
  有產生原生嵌入，訊息裡只剩一行含大量追蹤參數的醜陋純文字連結，
  沒有任何播放器或來源資訊）：原本 _build_fixed_video_link() 是對
  「整個原始網址」做網域替換，但透過 Bilibili App 分享按鈕複製的
  連結，會附帶大量追蹤參數（buvid、from_spmid、mid、
  share_session_id、unique_k、up_id 等），原封不動轉貼到
  vxbilibili.com 後，該服務未必能正確處理。改為只用已解析出的
  bvid 組出最簡潔的網址（https://vxbilibili.com/video/{bvid}/），
  不帶任何多餘查詢參數。連帶移除只有這個舊做法在用的
  _BILI_DOMAIN_RE（改用 bvid 後不再需要對原始網址做正規表示式
  網域替換）。
- 修正「影片截取」的核心作法：原本呼叫 Bilibili playurl API 取得
  可下載的影片串流網址，下載到記憶體後再以 Discord 附件重新上傳。
  這個做法有兩個實際問題：(1) 受限於 link_preview.video_max_upload_mb
  （預設 8MB），Bilibili 影片只要稍微長一點、畫質高一點就會超過
  上限而下載失敗，靜默退回只顯示縮圖；(2) 下載＋上傳消耗 Bot
  自己的頻寬與時間。參考真實案例 FixTweetBot（一款成熟的公開
  Discord 連結修復 Bot，支援數十種平台，其中就包含 Bilibili）的
  BiliBiliLink 實作：它完全不下載影片，只是把網域替換成
  vxbilibili.com（bilibili.com → vxbilibili.com、b23.tv →
  vxb23.tv，字首規則相同），送出這個修復後的網址純文字，讓
  Discord 自己的爬蟲原生解析出可播放的影片嵌入。改用同樣的做法後，
  沒有檔案大小上限問題，也不需要下載＋上傳。
- 不再呼叫 core.link_preview.video.resolve_bilibili_play_url()
  （該函式與其對應的 Bilibili playurl API 呼叫已隨之移除，
  沒有其他呼叫端使用）。
- 併入使用者提供的 cogs/events/bilibili.py 的關鍵修正：Bilibili API
  在缺少 Referer / Origin 標頭時有機率回傳 412，因此改為每次請求都
  帶上固定的 _BILI_HEADERS，而不是散落在多處各自組 headers
- 新增時長（duration）解析，比照統計數字共用同一套 LinkStat 顯示邏輯
- 不採用同步 requests：Discord 事件迴圈是非同步的，同步請求會整個
  卡住 Bot，維持既有的 httpx 非同步實作
- _format_duration 已整合至 utils.formatter.format_duration，移除
  本地重複實作
- _resolve_redirect 的短網址判斷改用 hostname 邊界比對（與
  detector.py 的修正方式一致），不再用粗糙的子字串搜尋

職責：

- 解析 b23.tv 短連結（跟隨重定向取得真正的 bilibili.com 網址）
- 呼叫 Bilibili 公開 API（x/web-interface/view）取得影片標題、簡介、
  封面、時長、UP 主與統計數據，組裝成統一的 LinkPreview 結構
- 產生 vxbilibili.com 風格的修復連結（embed_video_link），供
  Cog 層當作純文字內容送出，讓 Discord 原生嵌入播放影片

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

    embed_video_link = None
    if get_flag("link_preview.bilibili_fetch_video", True):
        embed_video_link = _build_fixed_video_link(bvid)

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
        platform         = "bilibili",
        platform_label   = "BiliBili",
        source_label     = "BiliBili",
        url              = real_url,
        title            = data.get("title"),
        author           = (data.get("owner") or {}).get("name"),
        description      = data.get("desc"),
        thumbnail_url    = data.get("pic"),
        embed_video_link = embed_video_link,
        stats            = stats,
        color            = 0x00A1D6,
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


def _build_fixed_video_link(bvid: str) -> str:
    """
    組出 vxbilibili.com 風格的乾淨修復連結，只用 bvid，不沿用原始
    網址的其餘部分。

    修正：原本直接對整個 real_url 做網域替換（_BILI_DOMAIN_RE.sub），
    但透過 Bilibili App「分享」按鈕複製的連結，會附帶大量追蹤參數
    （buvid、from_spmid、mid、share_session_id、unique_k、up_id 等，
    實測過一條連結可以長達數百字元）。這些參數原封不動轉貼到
    vxbilibili.com 後，該服務未必能正確處理，實測結果是 Discord
    完全沒有產生原生嵌入，訊息裡只剩一行含大量雜訊參數、純文字、
    無法點出預覽的醜陋連結——與「讓 Discord 原生嵌入播放影片」這個
    目的完全相反。改為只用已經解析出的 bvid 組出最簡潔的網址
    （https://vxbilibili.com/video/{bvid}/），不帶任何多餘查詢參數，
    這正是 vxbilibili.com 這類服務設計上預期收到的網址格式。
    """
    return f"https://vxbilibili.com/video/{bvid}/"


def _format_count(value: int | None) -> str:
    """將數字轉換為「萬」單位顯示，未取得資料時顯示 '-'。"""
    if value is None:
        return "-"
    if value >= 10_000:
        text = f"{value / 10_000:.1f}萬"
        return text.replace(".0萬", "萬")
    return str(value)
