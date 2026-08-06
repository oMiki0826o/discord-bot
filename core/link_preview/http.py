"""
core/link_preview/http.py

職責：
- 提供 build_client()，回傳設定好的 httpx.AsyncClient 情境管理器，
  供所有平台擷取器共用，統一 User-Agent、逾時、重定向追隨等參數。
- 新增平台時直接使用 build_client()，不需各自組裝 httpx.AsyncClient。

Modification():

- build_client() 新增 follow_redirects 參數（預設 True，維持既有
  呼叫端行為不變）：core.link_preview.article 的 fetch_text() 需要
  對任意使用者提供的網址做 SSRF 防護，每一次重定向都必須重新驗證
  目標網址是否安全，因此需要能夠關閉 httpx 內建的自動追隨重定向，
  改為手動控制、逐跳驗證（見 url_guard.py 與 article.py 的說明）。
  bilibili／instagram／threads／twitter／tiktok／pinterest 這些
  擷取器只會連到程式碼或 settings.json 裡設定好的固定代理網域，
  不受使用者輸入控制，不需要這層驗證，維持原本的 follow_redirects=True
  即可，不需要修改呼叫端。

- 新增本檔案：整合 core.link_preview 的 HTTP 客戶端設定，
  取代各擷取器各自建立 client 的分散實作。
- follow_redirects=True：Bilibili 短連結（b23.tv）、Pinterest 短連結
  （pin.it）皆需要追隨重定向至最終頁面，統一於此處開啟。
- 逾時秒數由 settings.json link_preview.request_timeout_seconds 控制
  （預設 10 秒），避免等待回應緩慢的網站卡住事件迴圈。
- User-Agent 設定為常見瀏覽器字串，減少因 UA 為空或可疑而被
  網站拒絕服務的機率。

備註：
- httpx.AsyncClient 使用 async with 語法，每次呼叫 build_client()
  會建立新的連線池，不跨請求共用 TCP 連接；對於連結預覽這類
  低頻率、多目標網站的情境這是合適的。若未來有效能需求，可改為
  module-level 的常駐 client，但需配合 Cog cleanup 事件關閉。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx

from core.system.settings import get_int

# ── 預設 User-Agent ──────────────────────
# 使用常見桌面瀏覽器 UA，減少被目標網站拒絕服務的機率。
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


# ── 對外介面 ──────────────────────

@asynccontextmanager
async def build_client(
    *, follow_redirects: bool = True,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """
    建立設定好的 httpx.AsyncClient，以 async with 情境管理器使用。

    使用範例：
        async with build_client() as client:
            response = await client.get(url)

    已套用設定：
    - follow_redirects：預設 True，自動追隨 301/302/307/308 重定向；
      需要逐跳驗證重定向目標時（見 article.py）傳入 False
    - timeout：從 link_preview.request_timeout_seconds 讀取
    - headers.User-Agent：常見桌面瀏覽器 UA
    """
    timeout = get_int("link_preview.request_timeout_seconds", 10)
    async with httpx.AsyncClient(
        follow_redirects = follow_redirects,
        timeout          = httpx.Timeout(timeout),
        headers          = {"User-Agent": _USER_AGENT},
    ) as client:
        yield client
