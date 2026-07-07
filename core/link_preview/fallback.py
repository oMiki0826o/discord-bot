"""
core/link_preview/fallback.py

Modification():

- 新增本檔案：統一「多候選網域、失敗自動改用下一個」的請求邏輯，
  取代 instagram.py／threads.py 原本各自寫死單一網域的做法。
  背景：ddinstagram.com 曾出現 DNS 完全無法解析、fixthreads.net
  曾回傳 502 Bad Gateway，這類社群維運的反代服務生命週期不穩定
  是常態而非例外。單一網域寫死，該服務一失效整個平台的預覽就
  完全失效；改為候選清單＋依序嘗試，只要清單中還有一個能連上，
  功能就能繼續運作。

職責：

- try_hosts()：接受一個「給定 host 組出完整網址」的函式與候選
  host 清單，依序嘗試直到成功（2xx）為止；DNS 失敗、連線逾時、
  5xx 伺服器錯誤皆視為該候選暫時不可用，繼續嘗試下一個，
  全部候選都失敗才回傳 None
"""

from __future__ import annotations

import logging
from typing import Callable

import httpx

from core.link_preview.http import build_client

logger = logging.getLogger("bot.link_preview.fallback")


# ── 對外介面 ──────────────────────

async def try_hosts(
    build_url:      Callable[[str], str],
    hosts:          list[str],
    *,
    platform_label: str,
) -> httpx.Response | None:
    """
    依序嘗試多個候選 host，回傳第一個成功的回應。

    build_url 由呼叫端提供，決定「給定一個候選 host，最終要請求
    的完整網址」；每個平台的網域替換規則不同（例如 Instagram 是
    整段網域替換，Threads 需保留路徑），因此交由呼叫端組裝，
    本函式只負責依序嘗試與失敗記錄。
    """
    last_reason = "候選網域清單為空"

    async with build_client() as client:
        for host in hosts:
            url = build_url(host)
            try:
                response = await client.get(url)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                last_reason = f"{host} 回應 {exc.response.status_code}"
            except httpx.HTTPError as exc:
                last_reason = f"{host} 連線失敗（{type(exc).__name__}）"
            logger.warning(
                "[%s] 候選網域失敗，改用下一個 host=%s reason=%s",
                platform_label, host, last_reason,
            )

    logger.error(
        "[%s] 所有候選網域皆失敗，最終原因：%s", platform_label, last_reason,
    )
    return None
