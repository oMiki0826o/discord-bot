"""
core/link_preview/video.py

Modification():

- 新增 Content-Type 驗證：代理服務（Instagram、Threads、Twitter、
  TikTok 的第三方反代）在異常狀況下有機率回傳 HTML 錯誤頁而非
  真正的影片二進位內容（例如網址已失效、需要登入才能存取等）。
  download_if_within_limit() 原本只檢查大小，沒有驗證下載到的
  內容是否真的是影片，會把錯誤頁當成影片上傳給 Discord，使用者
  收到一個完全無法播放的附件。現在下載完成後檢查回應標頭的
  Content-Type 是否為 video/*，不是則視為失敗並回傳 None，
  讓 Cog 層優雅退回「只顯示縮圖 + 連結」。
- download_if_within_limit() 從 Bilibili 專用擴展為所有平台共用
  的通用影片下載工具：Instagram、Threads、Twitter、TikTok 的
  og:video 標籤取得的直接影片網址，皆透過同一個函式下載，
  不需要各平台各自重複實作大小限制與內容驗證邏輯。
- download_if_within_limit() 改為回傳 (緩衝區, 副檔名) 而非單純
  緩衝區：不同平台回傳的影片實際容器格式不一定是 mp4（可能是
  webm、mov 等），若統一寫死副檔名為 .mp4，Discord 可能無法正確
  識別內容並提供內嵌播放器。現在依實際偵測到的 Content-Type
  決定副檔名，未知類型才回退為 mp4。

職責：

- resolve_bilibili_play_url()：透過 Bilibili 公開 playurl API 取得
  可直接播放的影片串流網址，供 bilibili.py 在設定開啟時附加影片
- download_if_within_limit()：下載影片至記憶體緩衝區，超過大小
  上限或內容不是影片格式時回傳 None，讓 Cog 層退回「只顯示縮圖 +
  連結」的呈現方式

背景說明：

- Discord 免費版伺服器的訊息附件上傳上限為 25MB，但 Bot 端連線
  與處理速度需留出緩衝，預設值設為 8MB（video_max_upload_mb），
  可由 settings.json 調整，支援未來 Discord 提升上限後無需修改
  程式碼。
- Bilibili playurl API 使用 qn=16（360P）最低畫質：目標是在
  Discord 內嵌播放，低畫質通常足夠，且檔案較小不易超出上限。
  設定 fnval=0 要求回傳舊版 flv 格式，相容性較廣；若未來 API
  移除 flv 支援，可改為 fnval=16（返回 dash mp4）並解析對應路徑。
- 下載前發送 HEAD 請求檢查 Content-Length，若已知超過上限則跳過
  下載；若 HEAD 未回傳 Content-Length 則繼續下載並在串流中追蹤
  累計大小，超過上限立即中止。
"""

from __future__ import annotations

import io
import logging

from core.link_preview.http import build_client
from core.system.settings import get_int

logger = logging.getLogger("bot.link_preview.video")

# ── Bilibili API ──────────────────────
_PLAY_URL_API = "https://api.bilibili.com/x/player/playurl"

_BILI_HEADERS = {
    "Referer": "https://www.bilibili.com/",
    "Origin":  "https://www.bilibili.com",
}


# ── Bilibili 播放網址 ──────────────────────

async def resolve_bilibili_play_url(bvid: str, cid: int) -> str | None:
    """
    呼叫 Bilibili playurl API，回傳可下載的影片串流網址。
    請求失敗、API 回傳錯誤或無影片網址時回傳 None。
    """
    params = {
        "bvid":  bvid,
        "cid":   cid,
        "qn":    16,    # 360P 最低畫質，確保檔案大小可接受
        "fnval": 0,     # 舊版 flv/mp4 格式（相容性最廣）
    }
    async with build_client() as client:
        try:
            response = await client.get(_PLAY_URL_API, params=params, headers=_BILI_HEADERS)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("[影片] Bilibili playurl 請求失敗 bvid=%s cid=%s", bvid, cid)
            return None

    if payload.get("code") != 0:
        logger.warning(
            "[影片] Bilibili playurl API 錯誤 bvid=%s code=%s msg=%s",
            bvid, payload.get("code"), payload.get("message"),
        )
        return None

    durl = payload.get("data", {}).get("durl", [])
    if not durl:
        logger.warning("[影片] Bilibili playurl 無影片網址 bvid=%s", bvid)
        return None

    return durl[0].get("url")


# ── 限制大小與格式驗證下載 ──────────────────────

# ── Content-Type 對應副檔名 ──────────────────────
# 依實際偵測到的格式決定附件副檔名，避免統一寫死 .mp4 導致 Discord
# 對非 mp4 格式（webm、quicktime 等）無法正確識別為可播放媒體。
_EXTENSION_BY_TYPE: dict[str, str] = {
    "video/mp4":        "mp4",
    "video/webm":       "webm",
    "video/quicktime":  "mov",
    "video/x-matroska": "mkv",
}
_DEFAULT_EXTENSION = "mp4"


async def download_if_within_limit(
    url: str, *, referer: str = "",
) -> tuple[io.BytesIO, str] | None:
    """
    下載影片至記憶體緩衝區；超過大小上限或內容非影片格式時回傳 None。

    成功時回傳 (緩衝區, 副檔名)；副檔名依實際偵測到的 Content-Type
    決定，未知類型一律回退為 mp4（相容性最廣的容器格式）。

    供 Bilibili 專屬邏輯與各平台代理服務的 og:video 共用，是唯一
    負責「網路下載 + 大小把關 + 格式把關」的地方，呼叫端不需要
    自行重複這些檢查。

    策略：
    1. 先以 HEAD 請求確認 Content-Length；已知超出上限則跳過下載。
    2. 以 GET 串流下載，累計大小超出上限立即中止。
    3. 下載完成後檢查 Content-Type 是否為 video/*；不是則視為
       下載到錯誤頁面或非預期內容，回傳 None。
    """
    limit_bytes = get_int("link_preview.video_max_upload_mb", 8) * 1024 * 1024
    headers: dict[str, str] = {}
    if referer:
        headers["Referer"] = referer

    async with build_client() as client:
        # ── HEAD 預檢 ──────────────────────
        try:
            head = await client.head(url, headers=headers)
            content_length = int(head.headers.get("content-length", 0))
            if content_length and content_length > limit_bytes:
                logger.info(
                    "[影片] 超過大小上限 size=%dMB limit=%dMB url=%s",
                    content_length // 1024 // 1024,
                    limit_bytes // 1024 // 1024,
                    url,
                )
                return None
        except Exception:
            pass  # HEAD 失敗時繼續嘗試下載，由串流大小追蹤控制

        # ── 串流下載 ──────────────────────
        try:
            buffer = io.BytesIO()
            async with client.stream("GET", url, headers=headers) as stream:
                stream.raise_for_status()

                content_type = stream.headers.get("content-type", "")
                if not _is_video_content_type(content_type):
                    logger.warning(
                        "[影片] 回應內容非影片格式，略過 content-type=%s url=%s",
                        content_type, url,
                    )
                    return None

                async for chunk in stream.aiter_bytes(chunk_size=65536):
                    buffer.write(chunk)
                    if buffer.tell() > limit_bytes:
                        logger.info("[影片] 下載中途超出大小上限 url=%s", url)
                        return None

            buffer.seek(0)
            extension = _EXTENSION_BY_TYPE.get(
                content_type.split(";")[0].strip().lower(), _DEFAULT_EXTENSION
            )
            return buffer, extension
        except Exception:
            logger.exception("[影片] 下載失敗 url=%s", url)
            return None


def _is_video_content_type(content_type: str) -> bool:
    """
    判斷回應標頭的 Content-Type 是否為影片格式。

    部分伺服器省略 Content-Type 或回傳不精確的值（例如
    application/octet-stream），此時保守放行交由後續大小與播放
    驗證把關，只明確擋下已知的非影片類型（text/html 等錯誤頁）。
    """
    if not content_type:
        return True
    lowered = content_type.lower()
    if lowered.startswith("video/"):
        return True
    return "octet-stream" in lowered
