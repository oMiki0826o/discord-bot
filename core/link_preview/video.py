"""
core/link_preview/video.py

職責：
- resolve_bilibili_play_url()：透過 Bilibili 公開 playurl API 取得
  可直接播放的影片串流 URL，供 bilibili.py 在設定開啟時附加影片。
- download_if_within_limit()：下載影片至記憶體緩衝區，超過上限時
  回傳 None，讓 Cog 層退回「只顯示縮圖 + 連結」的呈現方式。

Modification():

- 新增本檔案：集中影片相關的網路操作，讓平台擷取器只需呼叫統一
  介面，不需自行處理 API 路徑、大小檢查等細節。
- Discord 免費版伺服器的上傳上限為 25MB，Bot 實際可靠的上限約
  8MB；預設值設為 8MB（video_max_upload_mb），可由 settings.json
  調整，支援未來 Discord 提升上限後無需修改程式碼。
- Bilibili playurl API 使用 qn=16（360P）最低畫質：目標是在
  Discord 內嵌播放，低畫質通常足夠，且檔案較小不易超出上限。
  設定 fnval=0 要求回傳舊版 flv 格式，相容性較廣；若未來 API
  移除 flv 支援，可改為 fnval=16（返回 dash mp4）並解析對應路徑。
- 下載前發送 HEAD 請求檢查 Content-Length，若已知超過上限則跳過
  下載；若 HEAD 未回傳 Content-Length 則繼續下載並在串流中追蹤
  累計大小，超過上限立即中止。

備註：
- resolve_bilibili_play_url 使用與 bilibili.py 相同的 _BILI_HEADERS，
  避免 412 問題。

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


# ── Bilibili 播放 URL ──────────────────────

async def resolve_bilibili_play_url(bvid: str, cid: int) -> str | None:
    """
    呼叫 Bilibili playurl API，回傳可下載的影片串流 URL。
    請求失敗、API 回傳錯誤或無影片 URL 時回傳 None。
    """
    params = {
        "bvid": bvid,
        "cid":  cid,
        "qn":   16,    # 360P 最低畫質，確保檔案大小可接受
        "fnval": 0,    # 舊版 flv/mp4 格式（相容性最廣）
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
        logger.warning("[影片] Bilibili playurl 無影片 URL bvid=%s", bvid)
        return None

    return durl[0].get("url")


# ── 限制大小下載 ──────────────────────

async def download_if_within_limit(url: str, *, referer: str = "") -> io.BytesIO | None:
    """
    下載影片至記憶體緩衝區，超過 video_max_upload_mb 時回傳 None。

    策略：
    1. 先以 HEAD 請求確認 Content-Length；已知超出上限則跳過下載。
    2. 無 Content-Length 時繼續以 GET 串流下載，累計大小超出即中止。
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
                async for chunk in stream.aiter_bytes(chunk_size=65536):
                    buffer.write(chunk)
                    if buffer.tell() > limit_bytes:
                        logger.info(
                            "[影片] 下載中途超出大小上限 url=%s", url
                        )
                        return None
            buffer.seek(0)
            return buffer
        except Exception:
            logger.exception("[影片] 下載失敗 url=%s", url)
            return None
