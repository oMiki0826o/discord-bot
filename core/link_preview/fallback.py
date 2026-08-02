"""
core/link_preview/fallback.py

Modification():

- 新增「近期失敗網域短期冷卻」機制：log 顯示 Threads／Instagram
  的候選網域在同一次事件裡接連失敗，其中 www.vxthreads.net 是等到
  request_timeout_seconds（10 秒）逾時才判定失敗——這代表在該網域
  真正恢復之前，往後每一次有人分享連結，都會重新原地等 10 秒才
  換下一個候選，使用者體感延遲隨候選數量疊加。加入 _dead_until：
  失敗的 host 記錄「在這之後才值得再試」的時間點，之後一段時間內
  （預設 300 秒，見 settings.json 的
  link_preview.dead_host_cooldown_seconds）優先跳過、直接嘗試下一個
  候選，不用每次都重新等一次逾時。若「目前未在冷卻中」的候選一個
  都不剩（全部都失敗過），則放棄冷卻篩選、照樣嘗試全部候選，避免
  冷卻機制反而讓功能徹底停擺——冷卻只是「加速跳過已知會慢的選項」，
  不是「永久放棄」。
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
  全部候選都失敗才回傳 None。優先嘗試「近期沒失敗過」的候選。
"""

from __future__ import annotations

import logging
import time
from typing import Callable

import httpx

from core.link_preview.http import build_client
from core.system.settings import get_int

logger = logging.getLogger("bot.link_preview.fallback")

# ── 近期失敗網域的冷卻紀錄 ──────────────────────
# key=host, value=「在這個 monotonic 時間點之前，優先跳過此候選」。
# 使用 time.monotonic()（不受系統時鐘調整影響），且僅存在於行程記憶體
# 內：Bot 重啟後自動清空，不需要額外的持久化或清除邏輯。
_dead_until: dict[str, float] = {}


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
    if not hosts:
        logger.error("[%s] 候選網域清單為空", platform_label)
        return None

    now      = time.monotonic()
    cooldown = max(0, get_int("link_preview.dead_host_cooldown_seconds", 300))

    # 優先嘗試「目前不在冷卻中」的候選；若全部都在冷卻中，
    # 代表沒有更好的選擇，退回嘗試全部候選（見上方 Modification 說明）。
    candidates = [h for h in hosts if _dead_until.get(h, 0.0) <= now] or list(hosts)

    last_reason = "候選網域清單為空"

    async with build_client() as client:
        for host in candidates:
            url = build_url(host)
            try:
                response = await client.get(url)
                response.raise_for_status()
                _dead_until.pop(host, None)   # 成功了，解除這個 host 的冷卻標記
                return response
            except httpx.HTTPStatusError as exc:
                last_reason = f"{host} 回應 {exc.response.status_code}"
            except httpx.HTTPError as exc:
                last_reason = f"{host} 連線失敗（{type(exc).__name__}）"

            _dead_until[host] = now + cooldown
            logger.warning(
                "[%s] 候選網域失敗，改用下一個 host=%s reason=%s",
                platform_label, host, last_reason,
            )

    logger.error(
        "[%s] 所有候選網域皆失敗，最終原因：%s", platform_label, last_reason,
    )
    return None
